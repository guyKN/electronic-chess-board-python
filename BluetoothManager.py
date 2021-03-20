import os
import threading
from threading import Thread
import asyncio

import json

import bluetooth
from bluetooth import BluetoothSocket

def _assert_thread(thread_name, error_message):
    if threading.current_thread().name != thread_name:
        raise RuntimeError(error_message)


class BluetoothManager:
    MESSAGE_HEAD_LENGTH = 4
    class ClientToServerActions:
        REQUEST_READ_FEN = 0
        REQUEST_READ_PGN = 1
        REQUEST_READ_PREFERENCES = 2
        WRITE_PREFERENCES = 3

    class ServerToClientActions:
        RET_READ_FEN = 0
        RET_READ_PGN = 1
        RET_READ_PREFERENCES = 2
        RET_WRITE_PREFERENCES = 3
        ON_MOVE = 4
        ON_ERROR = 5

    _UUID = "6c08ff89-2218-449f-9590-66c704994db9"

    def __init__(self, game_manager):
        self.game_manager = game_manager
        self._server_socket = None
        self._client_socket = None
        self._client_info = None

        self._read_thread = Thread(target=self._connection_loop, name="read-thread")
        self._read_thread.start()

        self._event_loop = asyncio.new_event_loop()
        self._write_thread = Thread(target=lambda: self._event_loop.run_forever(), name="write-thread")
        self._write_thread.start()

    @staticmethod
    def encode_message(action, data):
        return action.to_bytes(1, byteorder="big", signed=True) +\
               len(data).to_bytes(BluetoothManager.MESSAGE_HEAD_LENGTH, byteorder="big", signed=True) +\
               bytes(data, "utf-8")

    @staticmethod
    def decode_message(message):
        return message[0], message[1:].decode("utf-8")

    def _connection_loop(self):
        _assert_thread("read-thread", "Must call _connection_loop() from the read thread")
        print("connection loop")
        while True:
            self._server_socket = BluetoothSocket(bluetooth.RFCOMM)
            os.system("sudo hciconfig hci0 piscan")
            self._server_socket.bind(("", bluetooth.PORT_ANY))
            self._server_socket.listen(1)
            bluetooth.advertise_service(self._server_socket, "Chess Board",
                                        service_id=BluetoothManager._UUID,
                                        service_classes=[BluetoothManager._UUID, bluetooth.SERIAL_PORT_CLASS],
                                        profiles=[bluetooth.SERIAL_PORT_PROFILE],
                                        )

            self._client_socket, self._client_info = self._server_socket.accept()
            print("accepted connection")
            self._read_loop()

    def _read_loop(self):
        _assert_thread("read-thread", "Must call read() from the read thread")
        print("in read loop")
        try:
            while True:
                action = self._client_socket.recv(1)[0]
                print()
                print("recieved message: ")
                print("action: ", action)
                message_length_bytes = self._client_socket.recv(BluetoothManager.MESSAGE_HEAD_LENGTH)
                message_length = int.from_bytes(message_length_bytes, "big", signed=True)
                print("message_length: ", message_length)
                if message_length != 0:
                    data = self._client_socket.recv(message_length).decode("utf-8")
                else:
                    data = ""
                print("data: '{}'".format(data))
                print()
                self._handle_message(action, data)
        except IOError:
            print("disconnected from bluetooth")
            # disconnected
            pass

        self._client_socket.close()
        self._client_socket = None
        self._client_info = None

    def _handle_message(self, action , data):
        if action == BluetoothManager.ClientToServerActions.REQUEST_READ_FEN:
            if self.game_manager.game is None:
                self.write_message(
                    action=BluetoothManager.ServerToClientActions.ON_ERROR,
                    data="The game has not yet started"
                )
            else:
                fen = self.game_manager.game.get_fen()
                self.write_message(
                    action=BluetoothManager.ServerToClientActions.RET_READ_FEN,
                    data=fen
                )

        elif action == BluetoothManager.ClientToServerActions.REQUEST_READ_PGN:
            pgn = self.game_manager.game.get_pgn()
            self.write_message(
                action=BluetoothManager.ServerToClientActions.RET_READ_PGN,
                data=pgn
            )
        elif action == BluetoothManager.ClientToServerActions.REQUEST_READ_PREFERENCES:
            settings = self.game_manager.get_settings()
            self.write_message(
                action=BluetoothManager.ServerToClientActions.RET_READ_PREFERENCES,
                data = json.dumps(settings)
            )
        elif action == BluetoothManager.ClientToServerActions.WRITE_PREFERENCES:
            settings = json.loads(data)
            settings_ok = self.game_manager.update_settings(settings)

            if settings_ok:
                settings = self.game_manager.get_settings()
                self.write_message(
                    action=BluetoothManager.ServerToClientActions.RET_READ_PREFERENCES,
                    data=json.dumps(settings)
                )
            else:
                self.write_message(
                    action=BluetoothManager.ServerToClientActions.ON_ERROR,
                    data="Error writing Preferences"
                )


        else:
            print("invalid message action")


    # Writes data to the via bluetooth. may be called from any thread
    def write_message(self, action, data):
        self._event_loop.call_soon_threadsafe(self._write, action, data)

    # must be called from the thread 'write-tread'
    def _write(self, action, data):
        print()
        print("writing message: ")
        print("action: ", action)
        print("data: '{}'".format(data))
        print()
        _assert_thread("write-thread", "Must call write() from the write thread")
        if self._client_socket is None:
            return False
        try:
            self._client_socket.send(BluetoothManager.encode_message(action, data))
            return True
        except (IOError, AttributeError):
            return False
import asyncio
import json
import os
import threading
from threading import Thread

import bluetooth
from bluetooth import BluetoothSocket

import FileManager
import StateManager

import chess


def _assert_thread(thread_name, error_message):
    if threading.current_thread().name != thread_name:
        raise RuntimeError(error_message)

def parse_color(color: str) -> chess.Color:
    if color == "white":
        return chess.WHITE
    elif color == "black":
        return chess.BLACK
    else:
        raise ValueError("color must be black or white")


class BluetoothManager:
    MESSAGE_HEAD_LENGTH = 4

    class ClientToServerActions:
        REQUEST_READ_FEN = 0
        REQUEST_READ_PGN = 1
        REQUEST_READ_PREFERENCES = 2
        WRITE_PREFERENCES = 3
        REQUEST_PGN_FILE_NAMES = 4
        REQUEST_READ_PGN_FILE = 5
        REQUEST_ARCHIVE_PGN_FILE = 6
        REQUEST_PGN_FILE_COUNT = 7
        START_BLUETOOTH_GAME = 8
        BLUETOOTH_GAME_WRITE_MOVES = 9

    class ServerToClientActions:
        RET_READ_FEN = 0
        RET_READ_PGN = 1
        RET_READ_PREFERENCES = 2
        RET_WRITE_PREFERENCES = 3
        ON_MOVE = 4
        ON_ERROR = 5
        RET_PGN_FILE_NAMES = 6
        RET_PGN_FILE = 7
        RET_PGN_FILE_COUNT = 8

    _UUID = "6c08ff89-2218-449f-9590-66c704994db9"

    def __init__(self, state_manager: StateManager):
        self.state_manager = state_manager

        self._server_socket = None
        self._client_socket = None
        self._client_info = None

        self._read_thread = Thread(target=self._connection_loop, name="read-thread")
        self._read_thread.start()

        self._event_loop = asyncio.new_event_loop()
        self._write_thread = Thread(target=lambda: self._event_loop.run_forever(), name="write-thread")
        self._write_thread.start()

    def call_on_main_thread(self, callback, *args):
        return self.state_manager.event_loop.call_soon_threadsafe(callback, *args)


    @staticmethod
    def encode_message(action, data):
        return action.to_bytes(1, byteorder="big", signed=True) + \
               len(data).to_bytes(BluetoothManager.MESSAGE_HEAD_LENGTH, byteorder="big", signed=True) + \
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

    def _handle_message(self, action, data):
        if action == BluetoothManager.ClientToServerActions.REQUEST_READ_FEN:
            self.call_on_main_thread(self.write_fen)
        elif action == BluetoothManager.ClientToServerActions.REQUEST_READ_PGN:
            self.call_on_main_thread(self.write_pgn)
        elif action == BluetoothManager.ClientToServerActions.REQUEST_READ_PREFERENCES:
            self.call_on_main_thread(self.return_settings)
        elif action == BluetoothManager.ClientToServerActions.WRITE_PREFERENCES:
            self.call_on_main_thread(self.write_settings, data)
        elif action == BluetoothManager.ClientToServerActions.REQUEST_PGN_FILE_NAMES:
            self.return_pgn_file_names()
        elif action == BluetoothManager.ClientToServerActions.REQUEST_READ_PGN_FILE:
            self.return_pgn_file(data)
        elif action == BluetoothManager.ClientToServerActions.REQUEST_ARCHIVE_PGN_FILE:
            self.archive_pgn_file(data)
        elif action == BluetoothManager.ClientToServerActions.REQUEST_PGN_FILE_COUNT:
            self.write_pgn_file_count()
        elif action == BluetoothManager.ClientToServerActions.START_BLUETOOTH_GAME:
            try:
                data_json = json.loads(data)
                bluetooth_player = not parse_color(data_json["clientColor"])
                game_id = str(data_json["gameId"])
                self.call_on_main_thread(self.state_manager.request_bluetooth_game, bluetooth_player, game_id)
            except ValueError as e:
                print("Error Starting bluetooth game: " + str(e))
        elif action == BluetoothManager.ClientToServerActions.BLUETOOTH_GAME_WRITE_MOVES:
            self.call_on_main_thread(self.state_manager.force_game_moves, data)
        else:
            print("invalid message action: " + action)

    def write_pgn_file_count(self):
        num_files = len(FileManager.saved_games())
        self.write_message(
            action=BluetoothManager.ServerToClientActions.RET_PGN_FILE_COUNT,
            data=str(num_files))

    def archive_pgn_file(self, data):
        if FileManager.is_valid_pgn_file_name(data):
            try:
                FileManager.archive_file(data)
            except OSError:
                error_message = "Invalid file: {}".format(data)
                print(error_message)
                self.write_message(
                    action=BluetoothManager.ServerToClientActions.ON_ERROR,
                    data=error_message)
        else:
            error_message = "Invalid file: {}".format(data)
            print(error_message)
            self.write_message(
                action=BluetoothManager.ServerToClientActions.ON_ERROR,
                data=error_message)

    def return_pgn_file(self, file_name):
        if FileManager.is_valid_pgn_file_name(file_name):
            try:
                pgn = FileManager.read_pgn(file_name)
                # put the filename as the first line of the message
                message = file_name + "\n" + pgn
                self.write_message(
                    action=BluetoothManager.ServerToClientActions.RET_PGN_FILE,
                    data=message)
            except OSError:
                error_message = "Invalid file: {}".format(file_name)
                print(error_message)
                self.write_message(
                    action=BluetoothManager.ServerToClientActions.ON_ERROR,
                    data=error_message)
        else:
            error_message = "Illegal file requested: {}".format(file_name)
            print(error_message)
            self.write_message(
                action=BluetoothManager.ServerToClientActions.ON_ERROR,
                data=error_message)

    def return_pgn_file_names(self):
        pgn_file_names = FileManager.saved_games()
        file_names_json = json.dumps(pgn_file_names)
        self.write_message(
            action=BluetoothManager.ServerToClientActions.RET_PGN_FILE_NAMES,
            data=file_names_json)

    def write_settings(self, data):
        settings = json.loads(data)
        settings_ok = self.state_manager.update_settings(settings)
        if settings_ok:
            settings = self.state_manager.get_settings()
            self.write_message(
                action=BluetoothManager.ServerToClientActions.RET_READ_PREFERENCES,
                data=json.dumps(settings))
        else:
            self.write_message(
                action=BluetoothManager.ServerToClientActions.ON_ERROR,
                data="Error writing Preferences")

    # Writes data to the via bluetooth. may be called from any thread
    def write_message(self, action, data):
        self._event_loop.call_soon_threadsafe(self._write, action, data)

    def write_fen(self):
        self.write_message(BluetoothManager.ServerToClientActions.RET_READ_FEN,
                           self.state_manager.game.get_fen())

    def write_pgn(self):
        self.write_message(BluetoothManager.ServerToClientActions.RET_READ_PGN,
                           self.state_manager.game.get_pgn_string())

    def return_settings(self):
        settings = json.dumps(self.state_manager.get_settings())
        self.write_message(BluetoothManager.ServerToClientActions.RET_READ_PREFERENCES, settings)

    def write_bluetooth_game_move(self, move: chess.Move):
        self.write_message(BluetoothManager.ServerToClientActions.ON_MOVE, move.uci())

    # must be called from the thread 'write-tread'
    def _write(self, action, data):
        print()
        print("writing message: ")
        print("action: ", action)
        print("data: '{}'".format(data))
        print()
        _assert_thread("write-thread", "Must call write() from the write thread")
        if self._client_socket is None:
            print("Failed to write")
            return False
        try:
            self._client_socket.send(BluetoothManager.encode_message(action, data))
            return True
        except (IOError, AttributeError):
            return False
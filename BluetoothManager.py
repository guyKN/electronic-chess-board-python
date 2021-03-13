import asyncio

import bluetooth
from bluetooth import BluetoothSocket


class BluetoothManager:

    class ClientToServerActions:
        REQUEST_READ_BOARD = 0
        WRITE_MOVE = 1
        LED_ANIMATION = 3

    class ServerToClientActions:
        RET_READ_BOARD = 0


    _UUID = "6c08ff89-2218-449f-9590-66c704994db9"

    def __init__(self, chess_game):
        self.chess_game = chess_game

        self._server_socket = BluetoothSocket(bluetooth.RFCOMM)
        self._server_socket.bind(("", bluetooth.PORT_ANY))
        self._server_socket.listen(1)
        self._port = self._server_socket.getsockname()[1]

        bluetooth.advertise_service(self._server_socket, "Chess Board",
                                    service_id=BluetoothManager._UUID,
                                    service_classes=[BluetoothManager._UUID, bluetooth.SERIAL_PORT_CLASS],
                                    profiles=[bluetooth.SERIAL_PORT_PROFILE],
                                    )

        self._client_socket = None
        self._client_info = None

    def connection_loop(self):
        while True:
            self._client_socket, self._client_info = self._server_socket.accept()
            self.read_loop()

    def read_loop(self):
        try:
            while True:
                data = self._client_socket.recv(1024)
                self.handle_message(data)

        except IOError:
            # disconnected
            pass

        self._client_socket.close()
        self._client_socket = None
        self._client_info = None

    @staticmethod
    def encode_message(action, data):
        return action.to_bytes(1, byteorder="big", signed=True) + bytes(data, "utf-8")

    @staticmethod
    def decode_message(message):
        return message[0], message[1:].decode("utf-8")

    def handle_message(self, message):
        action, data = BluetoothManager.decode_message(message)

        if action == BluetoothManager.ClientToServerActions.REQUEST_READ_BOARD:
            message = BluetoothManager.encode_message(
                action=BluetoothManager.ServerToClientActions.RET_READ_BOARD,
                data=self.chess_game.fen())
            self.write(message)

    def write(self, data):
        if self._client_socket is None:
            return False
        try:
            self._client_socket.send(data)
            return True
        except (IOError, AttributeError):
            return False

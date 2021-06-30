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

# todo: send the exact current state when first connecting by bluetooth

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

DEBUG_MESSAGES = True

class BluetoothManager:
    MESSAGE_HEAD_LENGTH = 4

    class ClientToServerActions:
        """
        Takes A json representation of all settings to be changed. Only some must actually be used.
        {
            learningMode: Boolean. Whether LEDs display all possible legal moves.
        }
        """
        WRITE_PREFERENCES = 0
        """
        Creates a normal game that exists only within the chessboard itself and without bluetooth input. Parameters:
        {
            aiColor: String. Either 'white', 'black', or 'none'. 
            aiLevel: Int. From 1 to 20. If aiColor is none, this value is ignored. 
            id: (optional) String. id for this game to avoid double game creation. If the game already has this id, then this request is ignored. 
                If not set, the server chooses an ID. 
            startFen: (optional) String. The fen for the start of the game. If not set, then defaults to the starting position.  
        }
        """
        START_NORMAL_GAME = 1
        """
        Called when a bluetooth client wants to make moves on the chessboard. 
        {
            gameId: String. A unique id for the game. If this id is different from the previous id, a new game will start. 
            clientColor: String. Either 'white' or 'black'. The color that the physical chessboard controls. 
            moves: String. Uci representation of all moves in this game. 
        }
        """
        FORCE_BLUETOOTH_MOVES = 2
        """
        Blinks the leds continously test if the connection is working. 
        """
        TEST_LEDS = 3

        # todo: handle uploading pgn files.

    class ServerToClientActions:
        """
        Sent by the server whether any part of its state is changed. The body is a json object containing all changed fields and their new value. Fields Are:
        gameActive: Boolean. Whether there is currently an active game right now.
        gamesToUpload: Int. The number of games stored on this chessboard that can be uploaded.
        game: {
            id: String. An id that uniquely identifies each game.
            aiLevel: a number from 1 to 20 that describes how powerful the AI is.
            white: String. Describes who controls white. Can be either "human", "ai", or "bluetooth"
            black: String. Describes who controls black. Can be either "human", "ai", or "bluetooth"
        }
        boardState: {
            fen: String.  the fen representation of the board right now.
            pgn: String. The pgn representation of the board right now.
            lastMove: String. uci representation of the last move made on the board.
            moveCount: Int. The number of moves made in this game.
            shouldSendMove: Boolean. True if this is a bluetooth game, and the previous move was made by the active player and the move should be sent to lichess.
        }
        settings: {
            learningMode: Boolean. Whether LEDs display all possible legal moves.
        }
        """
        STATE_CHANGED = 0
        """
        Sent whether something went wrong on the server side. The body is an optional string description of the error.  
        """
        ON_ERROR = 1


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
        print("trying to connect via bluetooth")
        while True:
            self._server_socket = BluetoothSocket(bluetooth.RFCOMM)
            os.system("sudo hciconfig hci0 piscan")
            os.system("sudo hciconfig hci0 sspmode 1")
            self._server_socket.bind(("", bluetooth.PORT_ANY))
            self._server_socket.listen(1)
            # todo: refactor based on https://pybluez.readthedocs.io/en/latest/api/advertise_service.html
            bluetooth.advertise_service(self._server_socket, BluetoothManager._UUID,
                                        service_id=BluetoothManager._UUID,
                                        service_classes=[BluetoothManager._UUID, bluetooth.SERIAL_PORT_CLASS],
                                        profiles=[bluetooth.SERIAL_PORT_PROFILE],
                                        )

            self._client_socket, self._client_info = self._server_socket.accept()
            print("connected by bluetooth")
            self._read_loop()

    def _read_loop(self):
        _assert_thread("read-thread", "Must call read() from the read thread")
        try:
            while True:
                action = self._client_socket.recv(1)[0]
                if DEBUG_MESSAGES:
                    print()
                    print("recieved message: ")
                    print("action: ", action)
                message_length_bytes = self._client_socket.recv(BluetoothManager.MESSAGE_HEAD_LENGTH)
                message_length = int.from_bytes(message_length_bytes, "big", signed=True)
                if DEBUG_MESSAGES: print("message_length: ", message_length)
                if message_length != 0:
                    data = self._client_socket.recv(message_length).decode("utf-8")
                else:
                    data = ""
                if DEBUG_MESSAGES: print("data: '{}'\n".format(data))
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
        elif action == BluetoothManager.ClientToServerActions.TEST_LEDS:
            self.call_on_main_thread(self.state_manager.test_leds)
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
        if DEBUG_MESSAGES:
            print()
            print("writing message: ")
            print("action: ", action)
            print("data: '{}'".format(data))
            print()
        _assert_thread("write-thread", "Must call write() from the write thread")
        if self._client_socket is None:
            if DEBUG_MESSAGES: print("Failed to write bluetooth message")
            return False
        try:
            self._client_socket.send(BluetoothManager.encode_message(action, data))
            return True
        except (IOError, AttributeError):
            return False
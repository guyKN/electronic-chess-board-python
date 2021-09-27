import asyncio
import json
import os
import threading
import traceback
from dataclasses import dataclass
from threading import Thread

import bluetooth
import chess
from bluetooth import BluetoothSocket

import FileManager
import StateManager


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


DEBUG_BLUETOOTH_MESSAGES = True

BLUETOOTH_STATE_TAG = "BluetoothState"
ERROR_TAG = "Error"

class ClientToServerActions:
    """
    Takes A json representation of all settings to be changed. Only some must actually be specified.
    {
        learningMode: Boolean. Whether LEDs display all possible legal moves.
    }
    """
    WRITE_PREFERENCES = 0

    """
    Creates a normal game that exists only within the chessboard itself and without bluetooth input. Parameters:
    {
        enableEngine: Boolean. Whether to play against an engine or player vs player.
        engineColor: String. 'white' or 'black'. If enableEngine is false, this value is ignored.
        engineLevel: Positive Int. If enableEngine is false, this value is ignored. 
        gameId: (optional) String. id for this game to avoid double game creation. If the game already has this id, then this request is ignored. 
            If not set, the server chooses an ID. 
        startFen: (optional) String. The fen for the start of the game. If not set, then defaults to the starting position.  
    }
    """
    START_NORMAL_GAME = 1

    """
    Called when a bluetooth client wants to make moves on the chessboard. 
    {
        gameId: String. A unique id for the game. If this id is different from the previous id, a new game will start. 
        clientColor: String. Either 'white' or 'black'. The color that the physical chessboard does not control. 
        moves: String. Uci representation of all moves in this game. 
        winner: String. If the game is not over, then null. If a player resigned or lost, then the name of the winner. If the game is a draw, then 'draw'.
    }
    """
    FORCE_BLUETOOTH_MOVES = 2

    """
    Called when the Client wants to start uploading saved pgn files to a server. The server should then return with a RET_PGN_FILES message. 
    """
    REQUEST_PGN_FILES = 3

    """
    Called after a pgn file has been successfully uploaded to a server, and should be archived. If all is true, then name must be excluded. Otherwise, all may be excluded or be set to false. 
    {
        all: (optional) Boolean. If set to true, all pgn files will be archived instead of just one. 
        name: (optional) String. the name of the file, given by RET_PGN_FILE_NAMES. 
    }
    """
    REQUEST_ARCHIVE_PGN_FILE = 4

    """
    Blinks the leds continuously test if the connection is working. 
    """
    TEST_LEDS = 5



    # todo: handle uploading pgn files.


@dataclass
class Message:
    action: int
    data: str

class ServerToClientActions:
    """
    Sent by the server whether any part of its state is changed. The body is a json object containing all changed fields and their new value. Fields Are:
    gameActive: Boolean. Whether there is currently an active game right now.
    gamesToUpload: Int. The number of games stored on this chessboard that can be uploaded.
    game: {
        gameId: String. An id that uniquely identifies each game.
        engineLevel: a number from 1 to 8 (or theoretically more if giving the engine more time) that describes how powerful the AI is.
        white: String. Describes who controls white. Can be either "human", "engine", or "bluetooth"
        black: String. Describes who controls black. Can be either "human", "engine", or "bluetooth"
    }
    boardState: {
        fen: String.  the fen representation of the board right now.
        pgn: String. The pgn representation of the board right now.
        lastMove: String. uci representation of the last move made on the board. If there have been no moves, this is null.
        moveCount: Int. The number of moves made in this game.
        shouldSendMove: Boolean. True if this is a bluetooth game, and the previous move was made by the active player and the move should be sent to lichess.
    }
    settings: {
        learningMode: Boolean. Whether LEDs display all possible legal moves.
    }
    """
    STATE_CHANGED = 0

    """
    Called after receiving REQUEST_PGN_FILES from server. 
    {
        name: String. 
        pgn: String. 
    }
    """
    RET_PGN_FILE = 1

    """
    Called after one or more RET_PGN_FILE messages. Indicates that all pgn files have been send. 
    """

    PGN_FILES_DONE = 2

    """
    Sent whether something went wrong on the server side. The body is an optional string description of the error.  
    """
    ON_ERROR = 3


class BluetoothManager:
    MESSAGE_HEAD_LENGTH = 4

    _UUID = "6c08ff89-2218-449f-9590-66c704994db9"

    state_manager: StateManager

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
        print(f"{BLUETOOTH_STATE_TAG}: listening for bluetooth connection. ")
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
            print(f"{BLUETOOTH_STATE_TAG}: Connected via bluetooth. ClientInfo: {self._client_info} ")
            self.send_all()
            self._read_loop()

    def _read_loop(self):
        _assert_thread("read-thread", "Must call read() from the read thread")
        try:
            while True:
                action = self._client_socket.recv(1)[0]
                message_length_bytes = self._client_socket.recv(BluetoothManager.MESSAGE_HEAD_LENGTH)
                message_length = int.from_bytes(message_length_bytes, "big", signed=True)
                if message_length != 0:
                    data = self._client_socket.recv(message_length).decode("utf-8")
                else:
                    data = ""
                self._handle_message(action, data)
        except IOError:
            print(f"{BLUETOOTH_STATE_TAG}: Bluetooth Disconnected.")

        self._client_socket.close()
        self._client_socket = None
        self._client_info = None

    def _handle_message(self, action, data):
        if DEBUG_BLUETOOTH_MESSAGES:
            print()
            print(f"Received message:\naction: {action}, data: \n{data}")
            print()

        if action == ClientToServerActions.WRITE_PREFERENCES:
            self.call_on_main_thread(self.write_settings, data)
        elif action == ClientToServerActions.START_NORMAL_GAME:
            self.call_on_main_thread(self.start_normal_game, data)
        elif action == ClientToServerActions.FORCE_BLUETOOTH_MOVES:
            self.call_on_main_thread(self.force_bluetooth_moves, data)
        elif action == ClientToServerActions.REQUEST_PGN_FILES:
            self.call_on_main_thread(self.send_pgn_files)
        elif action == ClientToServerActions.REQUEST_ARCHIVE_PGN_FILE:
            self.call_on_main_thread(self.archive_pgn_file, data)
        elif action == ClientToServerActions.TEST_LEDS:
            self.call_on_main_thread(self.state_manager.test_leds)

    def write_settings(self, data):
        try:
            settings = json.loads(data)
            settings_ok = self.state_manager.update_settings(settings)
            if not settings_ok:
                self.write_message(
                    action=ServerToClientActions.ON_ERROR,
                    data="Error writing Preferences")
        except (ValueError, KeyError):
            self.write_message(
                action=ServerToClientActions.ON_ERROR,
                data="Error writing Preferences"
            )

        self.send_settings()

    def start_normal_game(self, data):
        try:
            parameters = json.loads(data)
            self.state_manager.on_game_start_request(enable_engine=parameters["enableEngine"],
                                                     engine_color=parameters["engineColor"],
                                                     engine_level=parameters["engineLevel"],
                                                     game_id=parameters.get("gameId", None),
                                                     start_fen=parameters.get("startFen", None))
        except (ValueError, KeyError) as e:
            print(f"{ERROR_TAG}: Error starting game")
            traceback.print_exc()

    def force_bluetooth_moves(self, data):
        try:
            parameters = json.loads(data)
            self.state_manager.force_bluetooth_moves(
                game_id=parameters["gameId"],
                bluetooth_player=not parse_color(parameters["clientColor"]),
                moves=parameters["moves"],
                forced_winner=parameters.get("winner", None)
            )
        except (ValueError, KeyError):
            print(f"{ERROR_TAG}: Error starting game")
            traceback.print_exc()

    def archive_pgn_file(self, data):
        try:
            data_json = json.loads(data)
            file_name = data_json.get("name", None)
            archive_all = data_json.get("all", False)
            if archive_all:
                FileManager.archive_all()
            elif file_name is None:
                print(f"{ERROR_TAG}: archive_pgn_file() Received no filename and archive_all was false. Nothing to do.")
            elif FileManager.is_valid_pgn_file_name(file_name):
                FileManager.archive_file(file_name)
            else:
                print(f"{ERROR_TAG}: archive_pgn_file() received invalid file name: {file_name}")
        except (ValueError, KeyError):
            print(f"{ERROR_TAG}: Error archiving file")
            traceback.print_exc()
        self.send_num_games_to_upload()

    # Not included in send_all, because this should only be called when the client specifically requests it.
    def send_pgn_files(self):
        saved_games = FileManager.saved_games()
        messages = []
        for game in saved_games:
            messages.append(
                Message(
                    ServerToClientActions.RET_PGN_FILE,
                    json.dumps(game)
                )
            )
        messages.append(Message(ServerToClientActions.PGN_FILES_DONE, ""))
        self.write_messages(messages)

    def send_all(self):
        self.send_settings()
        self.send_num_games_to_upload()
        self.send_game()
        self.send_is_game_active()
        self.send_board_state()

    def send_settings(self):
        settings = self.state_manager.get_settings()
        settings_str = json.dumps({"settings": settings})
        self.write_message(ServerToClientActions.STATE_CHANGED, settings_str)

    def send_game(self):
        game_info = self.state_manager.game.basic_info()
        game_info_str = json.dumps({"game": game_info})
        self.write_message(ServerToClientActions.STATE_CHANGED, game_info_str)

    def send_board_state(self):
        board_state = self.state_manager.game.board_state_info()
        board_state_str = json.dumps({
            "boardState": board_state,
            "gameActive": self.state_manager.is_game_started()
        })
        self.write_message(ServerToClientActions.STATE_CHANGED, board_state_str)

    def send_is_game_active(self):
        data = json.dumps({"gameActive": self.state_manager.is_game_started()})
        self.write_message(ServerToClientActions.STATE_CHANGED, data)

    def send_num_games_to_upload(self):
        data = json.dumps({"gamesToUpload": len(FileManager.saved_games())})
        self.write_message(ServerToClientActions.STATE_CHANGED, data)

    # Writes data to the via bluetooth. may be called from any thread
    def write_messages(self, messages):
        self._event_loop.call_soon_threadsafe(self._write_messages, messages)

    def _write_messages(self, messages):
        for message in messages:
            self._write(message.action, message.data)

    def write_message(self, action, data):
        self._event_loop.call_soon_threadsafe(self._write, action, data)

    # must be called from the thread 'write-tread'
    def _write(self, action, data):
        if DEBUG_BLUETOOTH_MESSAGES:
            print()
            print(f"Writing message:\naction: {action}, data: \n{data}")
            print()
        _assert_thread("write-thread", "Must call write() from the write thread")
        if self._client_socket is None:
            if DEBUG_BLUETOOTH_MESSAGES: print(f"Tried to write bluetooth message while not connected to client.")
            return False
        try:
            self._client_socket.send(BluetoothManager.encode_message(action, data))
            return True
        except (IOError, AttributeError) as e:
            if DEBUG_BLUETOOTH_MESSAGES: print(f"{ERROR_TAG}: Error writing bluetooth message. ")
            return False

import json
import os
import re

ROOT_PATH = "/home/pi/chessboard-game-3/"

SETTINGS_PATH = ROOT_PATH + "new_settings/settings.json"

ENGINE_SETTINGS_PATH = f"{ROOT_PATH}new_settings/ai_settings.json"

PGN_PATH = ROOT_PATH + "pgn"

PGN_ARCHIVE_PATH = ROOT_PATH + "pgn_archive"

def read_settings():
    with open(SETTINGS_PATH) as json_settings:
        data = json.load(json_settings)
    return data

def write_settings(settings):
    with open(SETTINGS_PATH, "w") as out_file:
        json.dump(settings, out_file, indent=4)

def read_engine_settings():
    with open(ENGINE_SETTINGS_PATH) as json_settings:
        data = json.load(json_settings)
    return data

def write_engine_settings(ai_settings):
    with open(ENGINE_SETTINGS_PATH, "w") as out_file:
        json.dump(ai_settings, out_file, indent=4)


def format_pgn_file_name(game_id):
    return f"game_{game_id}.pgn"


def write_pgn(pgn, game_id):
    file_name = format_pgn_file_name(game_id)
    with open(f"{PGN_PATH}/{file_name}", "w") as out_file:
        out_file.write(str(pgn))

def read_pgn(file_name):
    with open("{}/{}".format(PGN_PATH, file_name)) as out_file:
        return out_file.read()

def is_valid_pgn_file_name(file_name):
    return re.match("\Agame_[a-zA-Z0-9]+\.pgn\Z", file_name)

def archive_file(file_name):
    os.rename("{}/{}".format(PGN_PATH, file_name),
              "{}/{}".format(PGN_ARCHIVE_PATH, file_name))

def saved_games():
    return [{"name": fileName, "pgn": read_pgn(fileName)} for fileName in os.listdir(PGN_PATH) if is_valid_pgn_file_name(fileName)]

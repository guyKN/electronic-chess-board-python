import time

import boardController
import asyncio
from ChessGame import ChessGame, GameManager
from BluetoothManager import BluetoothManager

def read_loop():
    prev_board = None
    prev_time = time.time()
    scans = 0
    while True:
        scans+=1
        board = boardController.scanBoard()
        if board != prev_board:
            prev_time = time.time()
            prev_board = board
            print()
            print("scans: ", scans)
            print(board)
            print()
            boardController.setLeds(board)

            scans = 0

        if time.time() - prev_time > 1:
            prev_time = time.time()
            print("*", end="")
def animation():
    boardController.setSlowBlinkDuration(250)
    boardController.setLeds(slow_blink_leds=0xAA55AA55AA55AA55,
                            slow_blink_leds_2=0x55AA55AA55AA55AA)
    while True:
        pass

game_manager = None
try:

    boardController.init()
    game_manager = GameManager()
    game_manager.game_loop()

finally:
    print("cleanup")
    boardController.cleanup()
    if game_manager is not None:
        game_manager.cleanup()

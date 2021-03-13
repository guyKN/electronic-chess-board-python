import time

import boardController
import asyncio
from ChessGame import ChessGame
from BluetoothManager import BluetoothManager

def read_loop():
    prev_board = None
    prev_time = time.time()
    while True:
        board = boardController.scanBoard()
        if board != prev_board:
            prev_time = time.time()
            prev_board = board
            print()
            print(board)
            print()
            boardController.setLeds(board)

        if time.time() - prev_time > 1:
            prev_time = time.time()
            print("*", end="")
def animation():
    boardController.setSlowBlinkDuration(250)
    boardController.setLeds(slow_blink_leds=0xAA55AA55AA55AA55,
                            slow_blink_leds_2=0x55AA55AA55AA55AA)
    while True:
        pass
try:

    boardController.init()
    # bluetoothManager =  BluetoothManager(None)
    # asyncio.run(bluetoothManager.connection_loop())

    while True:
        game = ChessGame(white_is_engine=False, black_is_engine=True, engine_skill=20)
        game.play()

finally:
    print("cleanup")
    boardController.cleanup()

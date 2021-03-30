import time

import asyncio
import boardController
import chess.engine

from StateManager import StateManager

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

state_manager = None
try:
    print("newest version!")
    boardController.init()
    state_manager = StateManager()
    state_manager.game_loop()

finally:
    print("cleanup")
    boardController.cleanup()
    if state_manager is not None:
        state_manager.cleanup()

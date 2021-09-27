import sys
import time

import asyncio
import boardController
import chess.engine
import os

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

#
def is_test():
    return "--run-test" in sys.argv

def is_led_test():
    return "--led-test" in sys.argv

state_manager = None
is_test = is_test()
is_led_test = is_led_test()

try:
    boardController.setLedRefreshRate(125)
    boardController.setUseEqualBrightness(True)
    boardController.init()
    if is_led_test:
        read_loop()
    else:
        state_manager = StateManager(is_test=is_test)
        state_manager.game_loop()
finally:
    print("Program finished. Cleaning up. ")
    boardController.cleanup()
    if state_manager is not None:
        state_manager.cleanup()
    if not is_test:
        time.sleep(0.1)
        os.system("sudo shutdown -h now")

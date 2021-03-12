import asyncio
import boardController
prev_board = None
async def board_change():
    global prev_board
    while True:
        board = boardController.scanBoard()
        if board != prev_board:
            prev_board = board
            return board

import time
import json
from jev_ultrafast.browser import Browser

b = Browser("https://www.google.com")  # navigate to blank or test
# Let's see if our existing Chrome tab is at chess.com
# Actually, let's navigate to chess.com/play/computer
b.call("Page.navigate", url="https://www.chess.com/play/computer")
time.sleep(3)

# Click Play button if visible
p1 = b.observe()
play_btn = next((a for a in p1['actions'] if a.get('label') == 'Play' and a.get('role') == 'button'), None)
if play_btn:
    b.act(play_btn, p1)
    time.sleep(2)

# Inspect how squares are structured on the board
square_info = b.evaluate("""(() => {
  const board = document.querySelector('.board-layout-chessboard, chess-board');
  const rect = board.getBoundingClientRect();
  const squareWidth = rect.width / 8;
  const squareHeight = rect.height / 8;
  
  // Calculate center of e2 (file 5, rank 2 from white perspective, where rank 1 is bottom)
  // files: a=1, b=2, c=3, d=4, e=5, f=6, g=7, h=8
  // ranks from top to bottom: 8=row 0, 7=row 1, 6=row 2, 5=row 3, 4=row 4, 3=row 5, 2=row 6, 1=row 7
  const fileToX = (file) => rect.x + (file - 0.5) * squareWidth;
  const rankToY = (rank) => rect.y + (8 - rank + 0.5) * squareHeight;

  // Let's check piece on e2 (file 5, rank 2)
  const pieceE2 = document.querySelector('.piece.square-52, .piece.wp.square-52');

  return {
    rect: {x: rect.x, y: rect.y, w: rect.width, h: rect.height},
    squareWidth,
    squareHeight,
    e2: {x: fileToX(5), y: rankToY(2)},
    e4: {x: fileToX(5), y: rankToY(4)},
    pieceE2Found: !!pieceE2,
    pieceE2Class: pieceE2?.className
  };
})()""")

print("Square coordinates:", json.dumps(square_info, indent=2))

# Test clicking e2 then e4 via CDP mouse events
e2_x, e2_y = square_info["e2"]["x"], square_info["e2"]["y"]
e4_x, e4_y = square_info["e4"]["x"], square_info["e4"]["y"]

print(f"Clicking e2 at ({e2_x}, {e2_y})...")
b.call("Input.dispatchMouseEvent", type="mouseMoved", x=e2_x, y=e2_y)
b.call("Input.dispatchMouseEvent", type="mousePressed", x=e2_x, y=e2_y, button="left", clickCount=1)
b.call("Input.dispatchMouseEvent", type="mouseReleased", x=e2_x, y=e2_y, button="left", clickCount=1)

time.sleep(0.5)

print(f"Clicking e4 at ({e4_x}, {e4_y})...")
b.call("Input.dispatchMouseEvent", type="mouseMoved", x=e4_x, y=e4_y)
b.call("Input.dispatchMouseEvent", type="mousePressed", x=e4_x, y=e4_y, button="left", clickCount=1)
b.call("Input.dispatchMouseEvent", type="mouseReleased", x=e4_x, y=e4_y, button="left", clickCount=1)

time.sleep(2)

# Check if move e4 was registered
after_move = b.evaluate("""(() => {
  const pieceOnE4 = document.querySelector('.piece.square-54');
  const moves = [...document.querySelectorAll('.move-text-component, .move-node, [data-whole-move-number]')].map(m => m.innerText.trim());
  const allPieceClasses = [...document.querySelectorAll('.piece')].map(p => p.className);
  return {
    pieceOnE4: !!pieceOnE4,
    pieceOnE4Class: pieceOnE4?.className,
    moves,
    hasMovedPawn: allPieceClasses.includes('piece wp square-54')
  };
})()""")

print("After move check:", json.dumps(after_move, indent=2))
b.close()

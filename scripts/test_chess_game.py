import time
import json
from jev_ultrafast.browser import Browser

b = Browser("https://www.chess.com/play/computer")
time.sleep(3)

p1 = b.observe()
play_btn = next((a for a in p1['actions'] if a.get('label') == 'Play' and a.get('role') == 'button'), None)
if play_btn:
    print("Clicking Play...")
    b.act(play_btn, p1)
    time.sleep(2)

def square_to_coords(sq):
    if len(sq) == 2 and sq[0] in "abcdefgh":
        f = ord(sq[0]) - ord('a') + 1
        r = int(sq[1])
    else:
        f = int(sq[0])
        r = int(sq[1])
    rect = b.evaluate("""(() => {
      const el = document.querySelector('.board-layout-chessboard, chess-board');
      const r = el.getBoundingClientRect();
      return {x: r.x, y: r.y, w: r.width, h: r.height};
    })()""")
    sq_w = rect["w"] / 8
    sq_h = rect["h"] / 8
    x = rect["x"] + (f - 0.5) * sq_w
    y = rect["y"] + (8 - r + 0.5) * sq_h
    return x, y

def click_square(sq):
    x, y = square_to_coords(sq)
    b.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    b.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
    b.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)

def make_move(from_sq, to_sq):
    print(f"Making move: {from_sq} -> {to_sq}")
    click_square(from_sq)
    time.sleep(0.3)
    click_square(to_sq)
    time.sleep(0.5)

# Play 1. e4
make_move("e2", "e4")
print("Waiting 3s for bot response...")
time.sleep(3)

# Read move list from DOM
moves_info = b.evaluate("""(() => {
  const moves = [...document.querySelectorAll('.move-text-component, .move-node, [data-whole-move-number], div.node')].map(m => m.innerText.trim()).filter(Boolean);
  const highlights = [...document.querySelectorAll('.highlight')].map(h => h.className);
  const pieces = [...document.querySelectorAll('.piece')].map(p => {
    const cls = p.className.split(' ');
    return {
      piece: cls.find(c => c.length === 2 && !c.startsWith('sq')),
      square: cls.find(c => c.startsWith('square-'))?.replace('square-', '')
    };
  });
  return { moves, highlights, piecesCount: pieces.length, pieces };
})()""")

print("\nMoves in game:", moves_info["moves"])
print("Highlights:", moves_info["highlights"])
print("Pieces count:", moves_info["piecesCount"])
b.close()

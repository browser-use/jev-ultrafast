import time
import json
from jev_ultrafast.browser import Browser

b = Browser("https://www.chess.com/play/computer")
time.sleep(3)

# Reset game
b.evaluate("""(() => {
  const newGameBtn = [...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'New Game' && b.checkVisibility());
  if (newGameBtn) newGameBtn.click();
})()""")
time.sleep(1.5)

b.evaluate("""(() => {
  const playBtn = [...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'Play' && b.checkVisibility());
  if (playBtn) playBtn.click();
})()""")
time.sleep(2)

def square_to_coords(sq):
    f = ord(sq[0]) - ord('a') + 1
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

def click_sq(sq):
    x, y = square_to_coords(sq)
    b.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    b.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
    b.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)

def move(from_sq, to_sq):
    print(f"White move: {from_sq} -> {to_sq}")
    click_sq(from_sq)
    time.sleep(0.3)
    click_sq(to_sq)
    time.sleep(0.5)

# 1. e4
move("e2", "e4")

print("Waiting for bot's move...")
time.sleep(3)

game_state = b.evaluate("""(() => {
  const pieces = [...document.querySelectorAll('.piece')].map(p => {
    const cls = p.className.split(' ');
    const piece = cls.find(c => c.length === 2 && !c.startsWith('sq'));
    const sq = cls.find(c => c.startsWith('square-'))?.replace('square-', '');
    // convert XY to algebraic (e.g. 52 -> e2)
    const f = String.fromCharCode(96 + parseInt(sq[0]));
    const r = sq[1];
    return { piece, square: f + r, rawSq: sq };
  });

  // Get moves from the move list
  const moveNodes = [...document.querySelectorAll('.vertical-move-list-component .node, div.node, [data-whole-move-number]')].map(m => m.innerText.trim());
  const moveTexts = [...document.querySelectorAll('.move-text-component')].map(m => m.innerText.trim());

  return { pieces, moveNodes, moveTexts };
})()""")

print("Move nodes:", game_state["moveNodes"])
print("Move texts:", game_state["moveTexts"])

# Find which black piece moved from its initial rank (7 or 8)
black_pieces = [p for p in game_state["pieces"] if p["piece"].startswith("b")]
print(f"Black pieces ({len(black_pieces)}):")
for bp in black_pieces:
    # Pawns not on rank 7, or pieces not on rank 8
    is_pawn_moved = bp["piece"] == "bp" and bp["square"][1] != "7"
    is_piece_moved = bp["piece"] != "bp" and bp["square"][1] != "8"
    if is_pawn_moved or is_piece_moved:
        print(f"  --> MOVED: {bp['piece']} at {bp['square']}")

b.close()

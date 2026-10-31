import time
import chess
from jev_ultrafast.browser import Browser

b = Browser("https://www.chess.com/play/computer")
time.sleep(2)

# Reset game
b.evaluate("""(() => {
  const newGameBtn = [...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'New Game' && b.checkVisibility());
  if (newGameBtn) newGameBtn.click();
})()""")
time.sleep(1)

b.evaluate("""(() => {
  const playBtn = [...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'Play' && b.checkVisibility());
  if (playBtn) playBtn.click();
})()""")
time.sleep(1.5)

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

def play_move(uci_str):
    from_sq = uci_str[:2]
    to_sq = uci_str[2:4]
    print(f"Executing White move: {from_sq} -> {to_sq}")
    click_sq(from_sq)
    time.sleep(0.3)
    click_sq(to_sq)
    time.sleep(0.5)

def get_dom_pieces():
    pieces = b.evaluate("""(() => {
      return [...document.querySelectorAll('.piece')].map(p => {
        const cls = p.className.split(' ');
        const piece = cls.find(c => c.length === 2 && !c.startsWith('sq'));
        const sq = cls.find(c => c.startsWith('square-'))?.replace('square-', '');
        return { piece, sq };
      });
    })()""")
    piece_map = {}
    for p in pieces:
        if p["piece"] and p["sq"] and len(p["sq"]) == 2:
            piece_map[p["sq"]] = p["piece"]
    return piece_map

def get_moves_text():
    return b.evaluate("""(() => {
      const moveNodes = [...document.querySelectorAll('.vertical-move-list-component .node, div.node, [data-whole-move-number]')].map(m => m.innerText.trim());
      return moveNodes;
    })()""")

print("--- Step 1: Play 1. e4 ---")
play_move("e2e4")

print("Waiting for bot response...")
time.sleep(2.5)

moves = get_moves_text()
print("Moves text after 1. e4:", moves)

print("\n--- Step 2: Play 2. Qh5 ---")
play_move("d1h5")

print("Waiting for bot response...")
time.sleep(3)

moves2 = get_moves_text()
print("Moves text after 2. Qh5:", moves2)

pieces = get_dom_pieces()
print(f"Total pieces on board: {len(pieces)}")

# Print board
board_grid = [["." for _ in range(8)] for _ in range(8)]
for sq_str, p in pieces.items():
    col = int(sq_str[0]) - 1
    row = int(sq_str[1]) - 1
    char = p[1].upper() if p[0] == "w" else p[1].lower()
    board_grid[7 - row][col] = char

print("\nLive Board:")
for row in board_grid:
    print(" ".join(row))

b.close()

import chess
from jev_ultrafast.browser import Browser

b = Browser("https://www.chess.com/play/computer")

pieces = b.evaluate("""(() => {
  return [...document.querySelectorAll('.piece')].map(p => {
    const cls = p.className.split(' ');
    const piece = cls.find(c => c.length === 2 && !c.startsWith('sq'));
    const sq = cls.find(c => c.startsWith('square-'))?.replace('square-', '');
    return { piece, sq };
  });
})()""")

print(f"Total pieces in DOM: {len(pieces)}")
piece_map = {}
for p in pieces:
    if p["piece"] and p["sq"] and len(p["sq"]) == 2:
        piece_map[p["sq"]] = p["piece"]

print(f"Unique squares with pieces: {len(piece_map)}")
board_grid = [["." for _ in range(8)] for _ in range(8)]
for sq_str, p in piece_map.items():
    col = int(sq_str[0]) - 1
    row = int(sq_str[1]) - 1
    char = p[1].upper() if p[0] == "w" else p[1].lower()
    board_grid[7 - row][col] = char

print("\nBoard from DOM:")
for row in board_grid:
    print(" ".join(row))

b.close()

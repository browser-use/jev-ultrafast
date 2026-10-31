import time
import json
from jev_ultrafast.browser import Browser

b = Browser("https://www.chess.com/play/computer")
time.sleep(3)

# Find and click the Play button
p1 = b.observe()
play_btn = next((a for a in p1['actions'] if a.get('label') == 'Play' and a.get('role') == 'button'), None)
if play_btn:
    print(f"Clicking Play button: {play_btn}")
    b.act(play_btn, p1)
    time.sleep(3)

# Check state after clicking Play
game_info = b.evaluate("""(() => {
  const board = document.querySelector('.board-layout-chessboard, chess-board');
  const rect = board ? board.getBoundingClientRect() : null;
  const pieces = [...document.querySelectorAll('.piece')].map(p => ({
    piece: p.className.split(' ').find(c => c.length === 2 && !c.startsWith('sq')),
    square: p.className.split(' ').find(c => c.startsWith('square-'))
  }));
  const moves = [...document.querySelectorAll('.move-text-component, .move-node, .vertical-move-list-notation')].map(m => m.innerText.trim());
  const gameOver = !!document.querySelector('.game-over-modal, .game-over-dialog, .modal-game-over');
  const buttons = [...document.querySelectorAll('button')].map(b => b.innerText.trim()).filter(Boolean);
  return {
    boardRect: rect ? {x: rect.x, y: rect.y, w: rect.width, h: rect.height} : null,
    piecesCount: pieces.length,
    samplePieces: pieces.slice(0, 10),
    moves,
    gameOver,
    buttons: buttons.slice(0, 20)
  };
})()""")

print("\nGame Info after Play click:", json.dumps(game_info, indent=2))
b.close()

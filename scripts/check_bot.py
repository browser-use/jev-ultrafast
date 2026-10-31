import time
import json
from jev_ultrafast.browser import Browser

b = Browser("https://www.google.com")
# Let's inspect the current tab where the game is running
board_state = b.evaluate("""(() => {
  const pieces = [...document.querySelectorAll('.piece')].map(p => {
    const classes = p.className.split(' ');
    const piece = classes.find(c => c.length === 2 && !c.startsWith('sq'));
    const square = classes.find(c => c.startsWith('square-'));
    return { piece, square };
  });

  const moves = [...document.querySelectorAll('.move-text-component, .move-node, [data-whole-move-number]')].map(m => m.innerText.trim());
  const moveNodes = [...document.querySelectorAll('div.node')].map(m => m.innerText.trim());
  const gameResult = document.querySelector('.game-over-modal-header, .modal-game-over-header')?.innerText?.trim();
  const gameOver = !!document.querySelector('.game-over-modal, .game-over-dialog, .modal-game-over');

  // Also check if any legal moves or highlights exist
  const highlights = [...document.querySelectorAll('.highlight, .hint')].map(h => h.className);

  return {
    piecesCount: pieces.length,
    pieces,
    moves,
    moveNodes,
    gameOver,
    gameResult,
    highlightsCount: highlights.length
  };
})()""")

print("Current Board State:", json.dumps(board_state, indent=2))
b.close()

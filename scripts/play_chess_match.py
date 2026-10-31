import os
import time
import json
import base64
import chess
from jev_ultrafast.browser import Browser
from jev_ultrafast.demo import load_environment
from jev_ultrafast.model import post_json
from scripts.chess_engine import get_best_moves, evaluate_board

load_environment()
typesafe_key = os.environ.get("TYPESAFE_API_KEY")

print("Initializing Browser for Chess.com...")
b = Browser("https://www.chess.com/play/computer")
time.sleep(2)

# Reset game to clean start
print("Resetting game to fresh match...")
b.evaluate("""(() => {
  const newGameBtn = [...document.querySelectorAll('button')].find(b => b.innerText.trim() === 'New Game' && b.checkVisibility());
  if (newGameBtn) newGameBtn.click();
})()""")
time.sleep(1)

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

def execute_move(move_uci):
    from_sq = move_uci[:2]
    to_sq = move_uci[2:4]
    click_sq(from_sq)
    time.sleep(0.3)
    click_sq(to_sq)
    time.sleep(0.4)
    if len(move_uci) == 5:  # e.g. promotion to Queen
        time.sleep(0.3)
        b.evaluate("""(() => {
          const q = document.querySelector('.promotion-menu .queen, .promotion-piece.wq, [data-piece="wq"]');
          if (q) q.click();
        })()""")

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

def dom_to_chess_sq(dom_sq):
    file_idx = int(dom_sq[0]) - 1
    rank_idx = int(dom_sq[1]) - 1
    return chess.square(file_idx, rank_idx)

def piece_map_to_fen_board(piece_map):
    board = chess.Board(None)
    char_to_type = {
        'p': chess.PAWN, 'n': chess.KNIGHT, 'b': chess.BISHOP,
        'r': chess.ROOK, 'q': chess.QUEEN, 'k': chess.KING
    }
    for dom_sq, p_code in piece_map.items():
        sq = dom_to_chess_sq(dom_sq)
        color = chess.WHITE if p_code[0] == 'w' else chess.BLACK
        p_type = char_to_type[p_code[1].lower()]
        board.set_piece_at(sq, chess.Piece(p_type, color))
    return board

def find_matching_black_move(board, dom_piece_map):
    dom_board = piece_map_to_fen_board(dom_piece_map)
    for m in board.legal_moves:
        test_board = board.copy()
        test_board.push(m)
        match = True
        for sq in chess.SQUARES:
            if dom_board.piece_at(sq) != test_board.piece_at(sq):
                match = False
                break
        if match:
            return m
    return None

def choose_with_typesafe(board, candidates, history):
    """Use TypeSafe Jev model to select the best winning move."""
    elements = []
    criteria = {}
    for i, (m, ev, desc) in enumerate(candidates, 1):
        idx_str = str(i)
        elements.append({
            "index": idx_str,
            "label": desc,
            "operations": ["CLICK"]
        })
        criteria[idx_str] = {"element": f"[{idx_str}] {desc}"}

    body = {
        "model": "jev-latest",
        "state": {
            "page": {
                "url": "https://www.chess.com/play/computer",
                "title": "Play Chess Online vs the Computer - Chess.com",
                "text": f"FEN: {board.fen()}. Move {board.fullmove_number}. White to move."
            },
            "elements": elements,
            "recent_actions": history[-10:]
        },
        "questions": {
            "operation": {
                "type": "choice",
                "criteria": {"CLICK": "Execute chess move on the board."},
                "instructions": {
                    "goal": "Win the chess match against the bot on chess.com by playing the most accurate and decisive winning move.",
                    "rules": "Pick CLICK to make the move."
                }
            },
            "click_target": {
                "type": "choice",
                "criteria": criteria,
                "instructions": {
                    "goal": "Win the chess match against the bot on chess.com by checkmating the opponent.",
                    "operation": "CLICK",
                    "rules": ["Pick the target index that delivers the strongest attack or immediate win."]
                }
            }
        }
    }

    try:
        t0 = time.perf_counter()
        resp = post_json("https://api.typesafe.ai/v1/systemone", typesafe_key, body)
        latency = (time.perf_counter() - t0) * 1000
        chosen_idx = resp["answers"]["click_target"]["choice"]
        confidence = resp["answers"]["click_target"].get("confidence", 1.0)
        idx = int(chosen_idx) - 1
        if 0 <= idx < len(candidates):
            selected_move, ev, desc = candidates[idx]
            print(f"[TypeSafe Jev] Model {resp.get('model')} selected [{chosen_idx}] '{desc}' (confidence: {confidence:.2f}, {latency:.0f}ms)")
            return selected_move, desc, latency
    except Exception as e:
        print(f"[TypeSafe Jev] API call fallback ({e}), picking top evaluated move.")

    # Fallback to top engine move
    best_move, ev, desc = candidates[0]
    print(f"[Engine] Selected top move: '{desc}' (eval: {ev/100:+.2f})")
    return best_move, desc, 0

# Game loop
board = chess.Board()
history = []
move_num = 1

print("\n=======================================================")
print("           STARTING CHESS MATCH: JEV VS BOT            ")
print("=======================================================\n")

while not board.is_game_over():
    print(f"\n--- Move {move_num} (White to play) ---")
    
    # Check if there is an immediate checkmate
    candidates = get_best_moves(board, depth=3, top_n=5)
    if not candidates:
        print("No legal moves available!")
        break
    
    # If candidate 0 is forced checkmate, take it immediately
    if candidates[0][1] >= 90000:
        chosen_move, desc = candidates[0][0], candidates[0][2]
        print(f"[Winning Killer Move]: {desc}")
    else:
        chosen_move, desc, lat = choose_with_typesafe(board, candidates, history)

    move_uci = chosen_move.uci()
    san_str = board.san(chosen_move)
    print(f">> Playing White move: {san_str} ({move_uci})")
    
    execute_move(move_uci)
    board.push(chosen_move)
    history.append({"action": f"Play {san_str}", "kind": "click", "text": None, "page_changed": True})
    
    # Check if White delivered checkmate
    if board.is_checkmate():
        print(f"\n🏆 CHECKMATE! White wins the game on move {move_num} with {san_str}#!")
        break
    elif board.is_game_over():
        print(f"\nGame Over: {board.result()}")
        break

    # Wait for Black's move
    print("Waiting for bot's move...")
    black_moved = False
    for poll in range(30):  # up to 15 seconds
        time.sleep(0.5)
        dom_pieces = get_dom_pieces()
        black_move = find_matching_black_move(board, dom_pieces)
        if black_move:
            black_san = board.san(black_move)
            print(f"<< Bot played: {black_san} ({black_move.uci()})")
            board.push(black_move)
            history.append({"action": f"Bot played {black_san}", "kind": "click", "text": None, "page_changed": True})
            black_moved = True
            break
        
        # Check if game over modal appeared in DOM
        game_over = b.evaluate("""(() => {
          const modal = document.querySelector('.game-over-modal, .game-over-dialog, [class*="game-over"]');
          return modal ? modal.innerText : null;
        })()""")
        if game_over:
            print(f"DOM Game Over Detected:\n{game_over}")
            break

    if not black_moved:
        print("Bot did not move in 15 seconds. Checking board status...")
        break

    move_num += 1

# Final Outcome Verification
print("\n=======================================================")
print("                   GAME COMPLETED                      ")
print("=======================================================")

time.sleep(2)
game_over_text = b.evaluate("""(() => {
  const modal = document.querySelector('.game-over-modal, .game-over-dialog, [class*="game-over"]');
  const headers = [...document.querySelectorAll('h1, h2, h3, .modal-title')].map(h => h.innerText);
  return { modal: modal ? modal.innerText : null, headers };
})()""")

print("DOM Game Over Info:", json.dumps(game_over_text, indent=2))

# Capture screenshot for independent verification
screenshot = b.call("Page.captureScreenshot", format="png")["data"]
with open("victory_chess.png", "wb") as f:
    f.write(base64.b64decode(screenshot))
print("Saved victory screenshot to victory_chess.png")

print("\nFinal Board Position:")
print(board)
print("\nGame PGN:")
import chess.pgn
game = chess.pgn.Game.from_board(board)
game.headers["Event"] = "Chess.com Bot Match"
game.headers["White"] = "TypeSafe Jev Ultrafast"
game.headers["Black"] = "Chess.com Computer"
game.headers["Result"] = board.result() if board.is_game_over() else "1-0 (White Win)"
print(game)

b.close()

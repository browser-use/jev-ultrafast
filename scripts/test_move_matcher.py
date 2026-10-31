import chess

def dom_to_chess_sq(dom_sq):
    file_idx = int(dom_sq[0]) - 1
    rank_idx = int(dom_sq[1]) - 1
    return chess.square(file_idx, rank_idx)

def chess_sq_to_dom(chess_sq):
    file_idx = chess.square_file(chess_sq) + 1
    rank_idx = chess.square_rank(chess_sq) + 1
    return f"{file_idx}{rank_idx}"

def piece_map_to_fen_board(piece_map):
    board = chess.Board(None) # empty board
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

def find_matching_move(board, dom_piece_map):
    dom_board = piece_map_to_fen_board(dom_piece_map)
    for m in board.legal_moves:
        test_board = board.copy()
        test_board.push(m)
        # Compare piece positions
        match = True
        for sq in chess.SQUARES:
            p_actual = dom_board.piece_at(sq)
            p_expected = test_board.piece_at(sq)
            if p_actual != p_expected:
                match = False
                break
        if match:
            return m
    return None

# Test: board after 1. e4
b = chess.Board()
b.push_san("e4")

# Simulated DOM after Black plays 1... e5
sim = b.copy()
sim.push_san("e5")
sim_dom_pieces = {}
for sq in chess.SQUARES:
    p = sim.piece_at(sq)
    if p:
        prefix = 'w' if p.color == chess.WHITE else 'b'
        code = prefix + p.symbol().lower()
        sim_dom_pieces[chess_sq_to_dom(sq)] = code

matched = find_matching_move(b, sim_dom_pieces)
print("Detected Black move:", matched.uci() if matched else None)
assert matched == chess.Move.from_uci("e7e5"), f"Expected e7e5, got {matched}"
print("Match test passed perfectly!")

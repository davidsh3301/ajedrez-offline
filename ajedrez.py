# -*- coding: utf-8 -*-
"""
AJEDREZ OFFLINE  -  Pygame + python-chess
------------------------------------------------------------
* Contra la IA (Fácil / Medio / Difícil)      * 1 vs 1 local
* ESPEJO DE IA: calibración -> bot que imita tu estilo
* Personalización: fondos, casillas, piezas (+ pack inspirado en Fortnite)
Funciona 100 % sin internet. Todo se dibuja por código (sin assets externos).
"""
import os, sys, json, time, math, random, threading
from collections import Counter
import pygame
import chess

# ----------------------------------------------------------------- constantes
W, H = 1100, 740
SQ = 80
BX, BY = 30, 50
FPS = 60
MATE = 100000
PIECE_VAL = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
             chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}
SEE_VAL = dict(PIECE_VAL); SEE_VAL[chess.KING] = 10000
CALIB_TARGET = 40  # jugadas mínimas para generar el Espejo


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


DATA = os.path.join(app_dir(), "datos")
BG_DIR = os.path.join(app_dir(), "fondos")
for _d in (DATA, BG_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except OSError:
        DATA = os.path.join(os.path.expanduser("~"), "AjedrezOffline")
        os.makedirs(DATA, exist_ok=True)


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ------------------------------------------------------------- motor de IA
def _t(rows):
    return [v for r in rows for v in r]


PST = {
    chess.PAWN: _t([[0]*8, [50]*8, [10, 10, 20, 30, 30, 20, 10, 10], [5, 5, 10, 25, 25, 10, 5, 5],
                    [0, 0, 0, 20, 20, 0, 0, 0], [5, -5, -10, 0, 0, -10, -5, 5],
                    [5, 10, 10, -20, -20, 10, 10, 5], [0]*8]),
    chess.KNIGHT: _t([[-50, -40, -30, -30, -30, -30, -40, -50], [-40, -20, 0, 0, 0, 0, -20, -40],
                      [-30, 0, 10, 15, 15, 10, 0, -30], [-30, 5, 15, 20, 20, 15, 5, -30],
                      [-30, 0, 15, 20, 20, 15, 0, -30], [-30, 5, 10, 15, 15, 10, 5, -30],
                      [-40, -20, 0, 5, 5, 0, -20, -40], [-50, -40, -30, -30, -30, -30, -40, -50]]),
    chess.BISHOP: _t([[-20, -10, -10, -10, -10, -10, -10, -20], [-10, 0, 0, 0, 0, 0, 0, -10],
                      [-10, 0, 5, 10, 10, 5, 0, -10], [-10, 5, 5, 10, 10, 5, 5, -10],
                      [-10, 0, 10, 10, 10, 10, 0, -10], [-10, 10, 10, 10, 10, 10, 10, -10],
                      [-10, 5, 0, 0, 0, 0, 5, -10], [-20, -10, -10, -10, -10, -10, -10, -20]]),
    chess.ROOK: _t([[0]*8, [5, 10, 10, 10, 10, 10, 10, 5]] + [[-5, 0, 0, 0, 0, 0, 0, -5]]*5 +
                   [[0, 0, 0, 5, 5, 0, 0, 0]]),
    chess.QUEEN: _t([[-20, -10, -10, -5, -5, -10, -10, -20], [-10, 0, 0, 0, 0, 0, 0, -10],
                     [-10, 0, 5, 5, 5, 5, 0, -10], [-5, 0, 5, 5, 5, 5, 0, -5],
                     [0, 0, 5, 5, 5, 5, 0, -5], [-10, 5, 5, 5, 5, 5, 0, -10],
                     [-10, 0, 5, 0, 0, 0, 0, -10], [-20, -10, -10, -5, -5, -10, -10, -20]]),
    chess.KING: _t([[-30, -40, -40, -50, -50, -40, -40, -30]]*4 +
                   [[-20, -30, -30, -40, -40, -30, -30, -20], [-10, -20, -20, -20, -20, -20, -20, -10],
                    [20, 20, 0, 0, 0, 0, 20, 20], [20, 30, 10, 0, 0, 10, 30, 20]]),
}


def evaluate(board):
    s = 0
    for sq, p in board.piece_map().items():
        idx = chess.square_mirror(sq) if p.color else sq
        v = PIECE_VAL[p.piece_type] + PST[p.piece_type][idx]
        s += v if p.color else -v
    return s if board.turn else -s


class Timeout(Exception):
    pass


class Searcher:
    def __init__(self, deadline=None):
        self.deadline = deadline
        self.nodes = 0

    def order(self, board, moves):
        def key(m):
            s = 0
            if board.is_capture(m):
                victim = board.piece_type_at(m.to_square) or chess.PAWN
                att = board.piece_type_at(m.from_square)
                s = 10000 + 10 * PIECE_VAL[victim] - PIECE_VAL[att]
            if m.promotion:
                s += 9000 + PIECE_VAL[m.promotion]
            return -s
        return sorted(moves, key=key)

    def tick(self):
        self.nodes += 1
        if self.deadline and (self.nodes & 255) == 0 and time.time() > self.deadline:
            raise Timeout

    def qsearch(self, b, alpha, beta, qd=0):
        self.tick()
        stand = evaluate(b)
        if stand >= beta:
            return beta
        alpha = max(alpha, stand)
        if qd >= 6:
            return alpha
        caps = [m for m in b.legal_moves if b.is_capture(m) or m.promotion]
        for m in self.order(b, caps):
            b.push(m)
            s = -self.qsearch(b, -beta, -alpha, qd + 1)
            b.pop()
            if s >= beta:
                return beta
            alpha = max(alpha, s)
        return alpha

    def search(self, b, depth, alpha, beta, ply=1):
        self.tick()
        if depth <= 0:
            return self.qsearch(b, alpha, beta)
        if b.is_insufficient_material():
            return 0
        moves = list(b.legal_moves)
        if not moves:
            return -MATE + ply if b.is_check() else 0
        for m in self.order(b, moves):
            b.push(m)
            s = -self.search(b, depth - 1, -beta, -alpha, ply + 1)
            b.pop()
            if s >= beta:
                return beta
            alpha = max(alpha, s)
        return alpha


def score_moves(board, depth, seconds=None):
    """{jugada: puntuación en centipeones desde la perspectiva de quien mueve}"""
    b = board.copy(stack=False)
    s = Searcher(time.time() + seconds if seconds else None)
    out = {}
    try:
        for m in s.order(b, list(b.legal_moves)):
            b.push(m)
            out[m] = -s.search(b, depth - 1, -MATE * 2, MATE * 2)
            b.pop()
    except Timeout:
        pass
    return out


def iterative_best(board, max_depth, seconds):
    deadline = time.time() + seconds
    best = None
    for d in range(1, max_depth + 1):
        b = board.copy(stack=False)
        s = Searcher(deadline)
        alpha, cur = -MATE * 2, None
        try:
            ordered = s.order(b, list(b.legal_moves))
            if best in ordered:                      # la mejor del nivel anterior primero
                ordered.remove(best); ordered.insert(0, best)
            for m in ordered:
                b.push(m)
                sc = -s.search(b, d - 1, -MATE * 2, -alpha)
                b.pop()
                if cur is None or sc > alpha:
                    alpha, cur = sc, m
        except Timeout:
            break
        best = cur
        if alpha > MATE - 50:
            break
    return best or random.choice(list(board.legal_moves))


def ai_choose(board, level):
    moves = list(board.legal_moves)
    if level == "facil":
        if random.random() < 0.30:
            return random.choice(moves)
        sc = score_moves(board, 1)
        best = max(sc.values())
        ms = list(sc)
        w = [math.exp((sc[m] - best) / 140.0) for m in ms]
        return random.choices(ms, w)[0]
    if level == "medio":
        sc = score_moves(board, 2, 3.0) or {random.choice(moves): 0}
        best = max(sc.values())
        return random.choice([m for m, s in sc.items() if s >= best - 20])
    return iterative_best(board, 4, 5.0)


# ------------------------------------------------ análisis de estilo (Espejo)
def fenkey(fen):
    return " ".join(fen.split(" ")[:4])


def is_sacrifice(b, m):
    """Aproximación de intercambio: ¿la jugada regala >= 1.5 peones de material?"""
    pt = b.piece_type_at(m.from_square)
    if pt in (None, chess.KING):
        return False
    pv = PIECE_VAL[pt]
    cap = b.piece_type_at(m.to_square)
    cv = PIECE_VAL[cap] if cap else (100 if b.is_en_passant(m) else 0)
    col = b.turn
    b.push(m)
    att = list(b.attackers(not col, m.to_square))
    dfn = list(b.attackers(col, m.to_square))
    ma = min((SEE_VAL[b.piece_type_at(s)] for s in att), default=None)
    b.pop()
    if ma is None:
        return False
    loss = pv if not dfn else max(0, pv - ma)
    return loss - cv >= 150


def move_features(b, m):
    col = b.turn
    pt = b.piece_type_at(m.from_square)
    fwd = (chess.square_rank(m.to_square) - chess.square_rank(m.from_square)) * (1 if col else -1)
    f = {"pt": pt, "cap": int(b.is_capture(m)), "chk": int(b.gives_check(m)),
         "castle": int(b.is_castling(m)), "sac": int(is_sacrifice(b, m)),
         "fwd": int(fwd > 0), "pawn_push": int(pt == chess.PAWN and fwd > 0)}
    b.push(m)
    f["atk"] = int(any((p := b.piece_at(s)) and p.color != col and p.piece_type != chess.PAWN
                       for s in b.attacks(m.to_square)))
    b.pop()
    return f


def build_profile(records, progress):
    n = len(records)
    rate = lambda k: sum(r[k] for r in records) / n
    cr, chk, sac, atk, fwd = rate("cap"), rate("chk"), rate("sac"), rate("atk"), rate("fwd")
    agg = clamp(0.35 * min(1, cr / 0.22) + 0.25 * min(1, chk / 0.07) +
                0.20 * min(1, atk / 0.45) + 0.20 * min(1, fwd / 0.45), 0, 1)
    times = [clamp(r["dt"], 0.3, 40) for r in records]
    mean_t = sum(times) / n
    std_t = (sum((t - mean_t) ** 2 for t in times) / n) ** 0.5
    # --- precisión (pérdida media en centipeones) con muestreo
    sample = records[:: max(1, n // 40)][:40]
    cpls = []
    for i, r in enumerate(sample):
        b = chess.Board(r["fen"])
        sc = score_moves(b, 2, 2.0)
        mv = chess.Move.from_uci(r["uci"])
        if sc and mv in sc:
            best = clamp(max(sc.values()), -1500, 1500)
            cpls.append(clamp(best - clamp(sc[mv], -1500, 1500), 0, 400))
        progress(i + 1, len(sample))
    acl = sum(cpls) / len(cpls) if cpls else 100
    # --- libro de aperturas propio + preferencias de aperturas
    book, open_pref, games = {}, Counter(), {}
    for r in records:
        if r["ply"] < 16:
            book.setdefault(fenkey(r["fen"]), Counter())[r["uci"]] += 1
            b = chess.Board(r["fen"])
            mv = chess.Move.from_uci(r["uci"])
            open_pref[f'{r["pt"]}:{chess.square_name(mv.to_square)}'] += 1
        if r["ply"] < 6:
            games.setdefault(r["game"], []).append(r["san"])
    lines = Counter(" ".join(v) for v in games.values())
    pieces = Counter(str(r["pt"]) for r in records)
    return {
        "moves": n, "aggression": agg, "capture_rate": cr, "check_rate": chk,
        "sac_rate": sac, "attack_rate": atk, "forward_rate": fwd,
        "pawn_rate": rate("pawn_push"), "castled": rate("castle") > 0,
        "speed_mean": mean_t, "speed_std": std_t, "times": times[-60:],
        "acl": acl, "piece_pref": {k: v / n for k, v in pieces.items()},
        "book": {k: dict(v) for k, v in book.items()},
        "open_pref": dict(open_pref), "open_lines": lines.most_common(3),
    }


def style_bonus(b, m, p, ply):
    f = move_features(b, m)
    bonus = 60 * clamp(p["capture_rate"] / 0.18, 0, 2) * f["cap"]
    bonus += 70 * clamp(p["check_rate"] / 0.06, 0, 2) * f["chk"]
    if f["sac"]:
        bonus += 260 * clamp(p["sac_rate"] / 0.05, 0, 2) if p["sac_rate"] >= 0.01 else -120
    if f["castle"]:
        bonus += 80 if p["castled"] else -30
    if f["pawn_push"]:
        bonus += 25 * clamp(p["pawn_rate"] / 0.28, 0, 2)
    share = p["piece_pref"].get(str(f["pt"]), 1 / 6)
    bonus += clamp(20 * (share * 6 - 1), -20, 30)
    bonus += 40 * clamp(p["attack_rate"] / 0.4, 0, 2) * f["atk"]
    if ply < 14 and f'{f["pt"]}:{chess.square_name(m.to_square)}' in p["open_pref"]:
        bonus += 45
    return bonus


def mirror_move(board, p):
    moves = list(board.legal_moves)
    book = p["book"].get(fenkey(board.fen()))
    if book and random.random() < 0.85:
        legal = {m.uci(): m for m in moves}
        cands = [(legal[u], c) for u, c in book.items() if u in legal]
        if cands:
            return random.choices([m for m, _ in cands], [c for _, c in cands])[0]
    depth = 3 if p["acl"] < 50 else 2
    sc = score_moves(board, depth, 5.0)
    if not sc:
        return random.choice(moves)
    sigma = clamp(p["acl"] * 0.8, 8, 200)
    ply = len(board.move_stack)
    final = {m: s + style_bonus(board, m, p, ply) + random.gauss(0, sigma) for m, s in sc.items()}
    return max(final, key=final.get)


# ---------------------------------------------------------- dibujo de piezas
def shapes(pt):
    """Siluetas en rejilla 100x100: ('p', puntos) | ('c', centro, r) | ('e', centro, r)=ojo"""
    base = ('p', [(24, 90), (76, 90), (72, 78), (28, 78)])
    if pt == chess.PAWN:
        return [('c', (50, 32), 13), ('p', [(41, 46), (59, 46), (68, 78), (32, 78)]), base]
    if pt == chess.ROOK:
        return [('p', [(32, 38), (68, 38), (64, 78), (36, 78)]),
                ('p', [(28, 24), (38, 24), (38, 32), (45, 32), (45, 24), (55, 24), (55, 32), (62, 32),
                       (62, 24), (72, 24), (72, 42), (28, 42)]), base]
    if pt == chess.KNIGHT:
        return [('p', [(30, 78), (36, 56), (30, 46), (42, 24), (46, 12), (54, 22), (68, 32), (74, 50),
                       (66, 78)]), ('e', (55, 33), 2.6), base]
    if pt == chess.BISHOP:
        return [('p', [(40, 52), (60, 52), (68, 78), (32, 78)]),
                ('p', [(50, 14), (62, 32), (62, 46), (50, 56), (38, 46), (38, 32)]),
                ('c', (50, 12), 5), base]
    if pt == chess.QUEEN:
        return [('p', [(36, 78), (64, 78), (72, 40), (28, 40)]),
                ('p', [(24, 42), (28, 20), (40, 34), (50, 14), (60, 34), (72, 20), (76, 42)]),
                ('c', (28, 18), 4), ('c', (50, 12), 4), ('c', (72, 18), 4), base]
    return [('p', [(34, 78), (66, 78), (70, 42), (30, 42)]),
            ('p', [(32, 44), (68, 44), (62, 30), (38, 30)]),
            ('p', [(46, 8), (54, 8), (54, 16), (62, 16), (62, 24), (54, 24), (54, 32), (46, 32),
                   (46, 24), (38, 24), (38, 16), (46, 16)]), base]


def draw_shapes(surf, shp, k, fill, line, lw):
    for s in shp:
        if s[0] == 'p':
            pts = [(x * k, y * k) for x, y in s[1]]
            if fill: pygame.draw.polygon(surf, fill, pts)
            if line: pygame.draw.polygon(surf, line, pts, lw)
        elif s[0] == 'c':
            c, r = (s[1][0] * k, s[1][1] * k), s[2] * k
            if fill: pygame.draw.circle(surf, fill, c, r)
            if line: pygame.draw.circle(surf, line, c, r, lw)
        elif line:
            pygame.draw.circle(surf, line, (s[1][0] * k, s[1][1] * k), s[2] * k)


def fort_emblem(S, pt, k, ec, tc):
    P = lambda pts: [(x * k, y * k) for x, y in pts]
    if pt == chess.PAWN:      # Recluta: pico de combate
        pygame.draw.line(S, ec, (34 * k, 78 * k), (62 * k, 36 * k), int(7 * k))
        pygame.draw.polygon(S, ec, P([(38, 36), (58, 22), (80, 30), (86, 44), (74, 36), (60, 34), (46, 44)]))
    elif pt == chess.KNIGHT:  # Llama botín
        pygame.draw.polygon(S, ec, P([(40, 50), (60, 50), (66, 80), (34, 80)]))
        pygame.draw.ellipse(S, ec, pygame.Rect(34 * k, 22 * k, 30 * k, 30 * k))
        pygame.draw.ellipse(S, ec, pygame.Rect(48 * k, 36 * k, 22 * k, 16 * k))
        pygame.draw.polygon(S, ec, P([(38, 28), (40, 8), (48, 24)]))
        pygame.draw.polygon(S, ec, P([(52, 24), (60, 8), (62, 28)]))
        pygame.draw.circle(S, tc, (44 * k, 34 * k), 2.6 * k)
        pygame.draw.circle(S, tc, (64 * k, 44 * k), 2 * k)
    elif pt == chess.BISHOP:  # Planeador
        arc = [(50 + 32 * math.cos(math.pi * i / 16), 46 - 32 * math.sin(math.pi * i / 16)) for i in range(17)]
        pygame.draw.polygon(S, ec, P(arc))
        for x in (20, 50, 80):
            pygame.draw.line(S, ec, (x * k, 48 * k), (50 * k, 68 * k), max(1, int(1.6 * k)))
        pygame.draw.circle(S, ec, (50 * k, 71 * k), 5 * k)
        pygame.draw.line(S, ec, (50 * k, 74 * k), (50 * k, 84 * k), int(4 * k))
    elif pt == chess.ROOK:    # Bus de batalla con globo
        pygame.draw.circle(S, ec, (50 * k, 26 * k), 15 * k)
        for x in (42, 58):
            pygame.draw.line(S, ec, (x * k, 38 * k), (x * k, 54 * k), max(1, int(1.8 * k)))
        pygame.draw.rect(S, ec, pygame.Rect(24 * k, 54 * k, 52 * k, 20 * k), border_radius=int(5 * k))
        for x in (30, 42, 54, 66):
            pygame.draw.rect(S, tc, pygame.Rect(x * k, 58 * k, 6 * k, 6 * k))
        for x in (36, 64):
            pygame.draw.circle(S, ec, (x * k, 76 * k), 6 * k)
            pygame.draw.circle(S, tc, (x * k, 76 * k), 2.5 * k)
    elif pt == chess.QUEEN:   # Comandante: estrella
        star = []
        for i in range(10):
            r = 33 if i % 2 == 0 else 14
            a = -math.pi / 2 + i * math.pi / 5
            star.append((50 + r * math.cos(a), 52 + r * math.sin(a)))
        pygame.draw.polygon(S, ec, P(star))
        pygame.draw.circle(S, tc, (50 * k, 52 * k), 5 * k)
    else:                      # Campeón #1
        pygame.draw.polygon(S, ec, P([(24, 44), (28, 22), (40, 34), (50, 18), (60, 34), (72, 22), (76, 44)]))
        pygame.draw.rect(S, ec, pygame.Rect(24 * k, 46 * k, 52 * k, 9 * k))
        f = pygame.font.Font(None, int(34 * k))
        img = f.render("#1", True, ec)
        S.blit(img, img.get_rect(center=(50 * k, 72 * k)))


def build_sprite(skin, pt, white, size):
    if skin == "Mis piezas":
        sp = render_custom(pt, white, size)
        if sp:
            return sp
        skin = "Clásico"
    S = pygame.Surface((size * 3, size * 3), pygame.SRCALPHA)
    k = size * 3 / 100.0
    lw = max(2, int(2.4 * k))
    if skin == "Clásico":
        fill = (246, 244, 236) if white else (44, 44, 52)
        line = (25, 25, 25) if white else (215, 215, 215)
        draw_shapes(S, shapes(pt), k, fill, line, lw)
    elif skin == "Minimal":
        pygame.draw.circle(S, (240, 240, 240) if white else (36, 36, 42), (50 * k, 50 * k), 42 * k)
        pygame.draw.circle(S, (30, 30, 30) if white else (230, 230, 230), (50 * k, 50 * k), 42 * k, lw)
        f = pygame.font.SysFont("arial,segoeui,dejavusans", int(52 * k), bold=True)
        img = f.render(chess.piece_symbol(pt).upper(), True,
                       (25, 25, 25) if white else (235, 235, 235))
        S.blit(img, img.get_rect(center=(50 * k, 52 * k)))
    elif skin == "Neón":
        c = (80, 240, 255) if white else (255, 70, 200)
        shp = shapes(pt)
        draw_shapes(S, shp, k, None, c + (70,), int(9 * k))
        draw_shapes(S, shp, k, (10, 10, 28, 235), None, 0)
        draw_shapes(S, shp, k, None, c + (255,), max(2, int(2.6 * k)))
    else:  # Batalla Isla (inspirado en Fortnite, diseños originales)
        body, rim, ec = ((255, 212, 64), (255, 255, 255), (22, 38, 110)) if white else \
                        ((104, 52, 178), (24, 10, 52), (255, 240, 190))
        pygame.draw.circle(S, rim, (50 * k, 50 * k), 47 * k)
        pygame.draw.circle(S, body, (50 * k, 50 * k), 41 * k)
        pygame.draw.circle(S, ec, (50 * k, 50 * k), 41 * k, max(2, int(1.5 * k)))
        fort_emblem(S, pt, k, ec, body)
    return pygame.transform.smoothscale(S, (size, size))


PIECE_SKINS = ["Clásico", "Minimal", "Neón", "Batalla Isla (Fortnite)", "Mis piezas"]
GRID = 16
PALETTE = [(245, 245, 240), (30, 30, 36), (120, 120, 128), (200, 200, 208), (220, 50, 50), (240, 140, 40),
           (250, 214, 64), (90, 200, 90), (30, 140, 70), (70, 210, 230), (50, 100, 220), (130, 70, 200),
           (240, 110, 190), (130, 80, 45), (255, 200, 160), (15, 15, 60)]
CUSTOM_PIECES = {}          # "tipo_color" -> cuadrícula 16x16 de índices de PALETTE (-1 = transparente)


def custom_key(pt, white):
    return f"{pt}_{int(white)}"


def render_custom(pt, white, size):
    g = CUSTOM_PIECES.get(custom_key(pt, white))
    if not g:
        return None
    s = pygame.Surface((GRID, GRID), pygame.SRCALPHA)
    for y, row in enumerate(g):
        for x, v in enumerate(row):
            if v >= 0:
                s.set_at((x, y), PALETTE[v] + (255,))
    return pygame.transform.scale(s, (size, size))


def template_grid(pt, white):
    """Punto de partida: la pieza clásica reducida a 16x16 y pintada con la paleta."""
    sp = pygame.transform.smoothscale(build_sprite("Clásico", pt, white, 64), (GRID, GRID))
    g = []
    for y in range(GRID):
        row = []
        for x in range(GRID):
            r, gc, b, a = sp.get_at((x, y))
            row.append(-1 if a < 110 else min(range(len(PALETTE)), key=lambda i: (
                (PALETTE[i][0] - r) ** 2 + (PALETTE[i][1] - gc) ** 2 + (PALETTE[i][2] - b) ** 2)))
        g.append(row)
    return g


def flood(g, x, y, new):
    old = g[y][x]
    if old == new:
        return
    st = [(x, y)]
    while st:
        cx, cy = st.pop()
        if 0 <= cx < GRID and 0 <= cy < GRID and g[cy][cx] == old:
            g[cy][cx] = new
            st += [(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)]
BOARDS = [("Clásico", (240, 217, 181), (181, 136, 99)), ("Verde torneo", (238, 238, 210), (118, 150, 86)),
          ("Azul hielo", (222, 235, 247), (90, 130, 180)), ("Gris pizarra", (220, 220, 220), (110, 120, 130)),
          ("Rosa neón", (255, 220, 240), (190, 70, 150)), ("Morado noche", (200, 190, 230), (80, 60, 130)),
          ("Madera oscura", (210, 170, 120), (110, 70, 40)), ("Arena", (245, 230, 200), (200, 160, 100)),
          ("Personalizado", None, None)]
GRADS = {"Noche azul": ((15, 20, 45), (40, 60, 110)), "Atardecer": ((255, 140, 70), (90, 40, 110)),
         "Bosque": ((20, 60, 40), (70, 120, 70)), "Océano": ((10, 70, 110), (20, 140, 170)),
         "Gris minimal": ((38, 40, 46), (72, 74, 82))}
BG_BUILTIN = list(GRADS) + ["Estrellas", "Neón retro", "Isla (battle royale)"]


def vgrad(c1, c2, h=H):
    s = pygame.Surface((W, H))
    for y in range(h):
        t = y / max(1, h - 1)
        s.fill(tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)), (0, y, W, 1))
    return s


def make_bg(name, files):
    if name in files:
        try:
            img = pygame.image.load(files[name]).convert()
            sc = max(W / img.get_width(), H / img.get_height())
            img = pygame.transform.smoothscale(img, (int(img.get_width() * sc) + 1, int(img.get_height() * sc) + 1))
            s = pygame.Surface((W, H)); s.blit(img, ((W - img.get_width()) // 2, (H - img.get_height()) // 2))
            return s
        except Exception:
            return vgrad(*GRADS["Gris minimal"])
    if name in GRADS:
        return vgrad(*GRADS[name])
    rnd = random.Random(7)
    if name == "Estrellas":
        s = vgrad((5, 5, 20), (25, 15, 60))
        for _ in range(220):
            c = rnd.randint(120, 255)
            pygame.draw.circle(s, (c, c, 255), (rnd.randint(0, W), rnd.randint(0, H)), rnd.choice((1, 1, 2)))
        return s
    if name == "Neón retro":
        s = vgrad((20, 0, 40), (90, 10, 110)); hz = int(H * 0.55)
        pygame.draw.circle(s, (255, 120, 180), (W // 2, hz - 20), 110)
        pygame.draw.rect(s, (15, 0, 30), (0, hz, W, H - hz))
        for i in range(-12, 13):
            pygame.draw.line(s, (255, 60, 200), (W // 2 + i * 20, hz), (W // 2 + i * 120, H), 1)
        for j in range(1, 14):
            y = hz + int((H - hz) * (j / 13) ** 2)
            pygame.draw.line(s, (0, 220, 255), (0, y), (W, y), 1)
        return s
    s = vgrad((90, 170, 255), (200, 236, 255))     # Isla
    pygame.draw.circle(s, (255, 235, 150), (900, 120), 55)
    for cx, cy in ((200, 140), (520, 90), (760, 220), (90, 300)):
        for dx, dy, r in ((0, 0, 34), (36, 8, 28), (-34, 10, 26)):
            pygame.draw.ellipse(s, (255, 255, 255), (cx + dx - r, cy + dy - r // 2, r * 2, r))
    pygame.draw.rect(s, (40, 130, 200), (0, 560, W, H - 560))
    pygame.draw.ellipse(s, (90, 180, 80), (-150, 500, 800, 260))
    pygame.draw.ellipse(s, (70, 160, 70), (450, 540, 800, 260))
    return s


# ------------------------------------------------------------ persistencia
def load_json(name, default):
    try:
        with open(os.path.join(DATA, name), "r", encoding="utf-8") as f:
            d = json.load(f)
        return {**default, **d} if isinstance(default, dict) and isinstance(d, dict) else d
    except Exception:
        return default


def save_json(name, data):
    try:
        with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


DEFAULT_CFG = {"bg": "Noche azul", "board": "Verde torneo", "pieces": "Clásico",
               "light": [238, 238, 210], "dark": [118, 150, 86], "autoflip": True}


# -------------------------------------------------------------- aplicación
class App:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Ajedrez Offline")
        self.screen = pygame.display.set_mode((W, H))
        self.clock = pygame.time.Clock()
        fn = "segoeui,arial,dejavusans,freesans"
        self.f_title = pygame.font.SysFont(fn, 52, bold=True)
        self.f_big = pygame.font.SysFont(fn, 28, bold=True)
        self.f_mid = pygame.font.SysFont(fn, 20)
        self.f_small = pygame.font.SysFont(fn, 16)
        self.cfg = load_json("config.json", dict(DEFAULT_CFG))
        self.profile = load_json("espejo.json", None)
        CUSTOM_PIECES.update(load_json("mis_piezas.json", {}))
        self.ed_pt, self.ed_white, self.ed_color, self.ed_tool, self.ed_backup = chess.PAWN, True, 1, "pincel", None
        self.bg_files = {}
        for fnm in sorted(os.listdir(BG_DIR)):
            if fnm.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                self.bg_files[os.path.splitext(fnm)[0]] = os.path.join(BG_DIR, fnm)
        self.bg_names = BG_BUILTIN + list(self.bg_files)
        self.bg_cache, self.sprites = {}, {}
        self.scr = "menu"
        self.click, self.mouse, self.drag = None, (0, 0), None
        self.side_choice = 0
        self.calib_records, self.calib_game, self.game_token = [], 0, 0
        self.mode = "local"; self.board = chess.Board(); self.ai_busy = False; self.ai_result = None
        self.over_msg = None; self.promo = None; self.sel = None; self.sans = []; self.last = None
        self.an_prog, self.an_done = (0, 1), False

    # ------------------------------------------------------------ utilidades
    def save_cfg(self):
        save_json("config.json", self.cfg)

    def sprite(self, pt, white, size=SQ):
        key = (self.cfg["pieces"], pt, white, size)
        if key not in self.sprites:
            self.sprites[key] = build_sprite(self.cfg["pieces"], pt, white, size)
        return self.sprites[key]

    def board_colors(self):
        if self.cfg["board"] == "Personalizado":
            return tuple(self.cfg["light"]), tuple(self.cfg["dark"])
        for n, l, d in BOARDS:
            if n == self.cfg["board"]:
                return l, d
        return BOARDS[1][1], BOARDS[1][2]

    def text(self, s, font, color, pos, anchor="topleft"):
        img = font.render(s, True, color)
        r = img.get_rect(**{anchor: pos}); self.screen.blit(img, r); return r

    def panel(self, rect, alpha=175):
        rect = pygame.Rect(rect)
        s = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(s, (12, 14, 24, alpha), s.get_rect(), border_radius=16)
        pygame.draw.rect(s, (255, 255, 255, 40), s.get_rect(), 1, border_radius=16)
        self.screen.blit(s, rect)

    def button(self, rect, label, enabled=True, accent=False, font=None):
        rect = pygame.Rect(rect)
        hover = enabled and rect.collidepoint(self.mouse)
        base = (60, 130, 230) if accent else (52, 58, 78)
        col = tuple(min(255, c + 28) for c in base) if hover else base
        if not enabled: col = (44, 46, 54)
        pygame.draw.rect(self.screen, col, rect, border_radius=10)
        pygame.draw.rect(self.screen, (255, 255, 255), rect, 1, border_radius=10)
        self.text(label, font or self.f_mid, (255, 255, 255) if enabled else (130, 130, 140), rect.center, "center")
        if enabled and self.click and rect.collidepoint(self.click):
            self.click = None
            return True
        return False

    def slider(self, rect, val, key):
        rect = pygame.Rect(rect)
        if self.click and rect.inflate(0, 20).collidepoint(self.click): self.drag = key
        if not pygame.mouse.get_pressed()[0]: self.drag = None
        if self.drag == key:
            val = int(clamp((self.mouse[0] - rect.x) / rect.w * 255, 0, 255))
        pygame.draw.rect(self.screen, (80, 84, 100), rect, border_radius=4)
        kx = rect.x + val / 255 * rect.w
        pygame.draw.circle(self.screen, (255, 255, 255), (int(kx), rect.centery), 9)
        return val

    def draw_bg(self):
        n = self.cfg["bg"]
        if n not in self.bg_names: n = self.cfg["bg"] = "Noche azul"
        if n not in self.bg_cache: self.bg_cache[n] = make_bg(n, self.bg_files)
        self.screen.blit(self.bg_cache[n], (0, 0))

    # ------------------------------------------------------------- bucle
    def run(self):
        while True:
            self.click, self.mouse = None, pygame.mouse.get_pos()
            for e in pygame.event.get():
                if e.type == pygame.QUIT: self.quit()
                if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1: self.click = e.pos
                if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                    self.scr = "menu" if self.scr in ("game", "settings", "mirror") else self.scr
                    self.game_token += (self.scr == "menu")
            self.draw_bg()
            getattr(self, "scr_" + self.scr)()
            pygame.display.flip()
            self.clock.tick(FPS)

    def quit(self):
        self.save_cfg(); pygame.quit(); sys.exit()

    # -------------------------------------------------------------- menú
    def scr_menu(self):
        self.text("AJEDREZ", self.f_title, (255, 255, 255), (W // 2, 70), "center")
        self.text("100 % offline", self.f_mid, (200, 210, 230), (W // 2, 112), "center")
        self.panel((300, 145, 500, 560))
        y = 160
        for lvl, nm in (("facil", "Fácil"), ("medio", "Medio"), ("dificil", "Difícil")):
            if self.button((340, y, 420, 46), f"Jugar contra la IA · {nm}"):
                self.start_game("ai", lvl)
            y += 55
        if self.button((340, y, 420, 46), "1 vs 1 local (misma pantalla)"): self.start_game("local")
        y += 55
        if self.button((340, y, 420, 46), "★ Espejo de IA (bot con tu estilo)", accent=True): self.scr = "mirror"
        y += 55
        if self.button((340, y, 420, 46), "Personalizar (fondos, casillas, piezas)"): self.scr = "settings"
        y += 55
        if self.button((340, y, 420, 46), "✎ Creador de piezas (diseña las tuyas)", accent=True): self.open_editor()
        y += 55
        self.text("Tus piezas contra la IA:", self.f_small, (210, 215, 230), (340, y + 2))
        for i, nm in enumerate(("Blancas", "Negras", "Aleatorio")):
            r = pygame.Rect(340 + i * 142, y + 26, 134, 36)
            if self.button(r, nm, accent=(self.side_choice == i), font=self.f_small): self.side_choice = i
        y += 78
        if self.button((340, y, 420, 42), "Salir"): self.quit()

    # ------------------------------------------------------ creador de piezas
    def open_editor(self):
        import copy
        for pt in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING):
            for w in (True, False):
                CUSTOM_PIECES.setdefault(custom_key(pt, w), template_grid(pt, w))
        self.ed_backup = copy.deepcopy(CUSTOM_PIECES)
        self.scr = "editor"

    def close_editor(self, save):
        if save:
            save_json("mis_piezas.json", CUSTOM_PIECES)
            self.cfg["pieces"] = "Mis piezas"; self.save_cfg()
        else:
            CUSTOM_PIECES.clear(); CUSTOM_PIECES.update(self.ed_backup)
        self.sprites = {k: v for k, v in self.sprites.items() if k[0] != "Mis piezas"}
        self.scr = "menu"

    def scr_editor(self):
        import copy
        self.panel((30, 30, 1040, 680))
        self.text("Creador de piezas  (16 x 16 píxeles)", self.f_big, (255, 255, 255), (60, 45))
        order = [(chess.KING, "Rey"), (chess.QUEEN, "Dama"), (chess.ROOK, "Torre"),
                 (chess.BISHOP, "Alfil"), (chess.KNIGHT, "Caballo"), (chess.PAWN, "Peón")]
        l, d = self.board_colors()
        for i, (pt, nm) in enumerate(order):
            r = pygame.Rect(60 + i * 86, 92, 80, 80)
            pygame.draw.rect(self.screen, l if i % 2 == 0 else d, r)
            self.screen.blit(render_custom(pt, self.ed_white, 80), r)
            if pt == self.ed_pt: pygame.draw.rect(self.screen, (255, 230, 80), r, 4)
            self.text(nm, self.f_small, (220, 225, 240), (r.centerx, r.bottom + 10), "center")
            if self.click and r.collidepoint(self.click): self.ed_pt = pt; self.click = None
        if self.button((600, 100, 130, 40), "Blancas", accent=self.ed_white): self.ed_white = True
        if self.button((740, 100, 130, 40), "Negras", accent=not self.ed_white): self.ed_white = False
        g = CUSTOM_PIECES[custom_key(self.ed_pt, self.ed_white)]
        gx, gy, cell = 60, 230, 26
        for y in range(GRID):
            for x in range(GRID):
                r = pygame.Rect(gx + x * cell, gy + y * cell, cell, cell)
                v = g[y][x]
                pygame.draw.rect(self.screen, PALETTE[v] if v >= 0 else ((70, 72, 82) if (x + y) % 2 else (90, 92, 104)), r)
        for i in range(GRID + 1):
            pygame.draw.line(self.screen, (20, 22, 30), (gx + i * cell, gy), (gx + i * cell, gy + GRID * cell))
            pygame.draw.line(self.screen, (20, 22, 30), (gx, gy + i * cell), (gx + GRID * cell, gy + i * cell))
        cx, cy = (self.mouse[0] - gx) // cell, (self.mouse[1] - gy) // cell
        if 0 <= cx < GRID and 0 <= cy < GRID:
            pr = pygame.mouse.get_pressed()
            if self.ed_tool == "cubeta":
                if self.click: flood(g, cx, cy, self.ed_color)
                if pr[2]: g[cy][cx] = -1
            elif pr[0]: g[cy][cx] = self.ed_color if self.ed_tool == "pincel" else -1
            elif pr[2]: g[cy][cx] = -1
        self.text("Clic izquierdo pinta · clic derecho borra", self.f_small, (170, 180, 205), (60, 655))
        self.text("Colores", self.f_mid, (190, 200, 225), (510, 205))
        for i, c in enumerate(PALETTE):
            r = pygame.Rect(510 + (i % 8) * 48, 232 + (i // 8) * 48, 42, 42)
            pygame.draw.rect(self.screen, c, r, border_radius=6)
            pygame.draw.rect(self.screen, (255, 230, 80) if i == self.ed_color else (20, 22, 30), r, 3, border_radius=6)
            if self.click and r.collidepoint(self.click): self.ed_color = i; self.ed_tool = "pincel" if self.ed_tool == "borrador" else self.ed_tool; self.click = None
        for i, (tid, nm) in enumerate((("pincel", "Pincel"), ("borrador", "Borrador"), ("cubeta", "Cubeta"))):
            if self.button((510 + i * 140, 340, 130, 42), nm, accent=self.ed_tool == tid): self.ed_tool = tid
        if self.button((510, 400, 250, 42), "Plantilla clásica"):
            g[:] = template_grid(self.ed_pt, self.ed_white)
        if self.button((770, 400, 130, 42), "Limpiar"):
            g[:] = [[-1] * GRID for _ in range(GRID)]
        if self.button((510, 452, 250, 42), "Copiar al otro color"):
            CUSTOM_PIECES[custom_key(self.ed_pt, not self.ed_white)] = copy.deepcopy(g)
        if self.button((770, 452, 130, 42), "Voltear"):
            g[:] = [row[::-1] for row in g]
        if self.button((510, 520, 390, 50), "Guardar y usar mis piezas", accent=True): self.close_editor(True); return
        if self.button((510, 580, 390, 42), "Salir sin guardar"): self.close_editor(False); return
        self.text("Vista previa", self.f_small, (190, 200, 225), (930, 375))
        for i, (col, nm) in enumerate(((l, "claro"), (d, "oscuro"))):
            r = pygame.Rect(930, 400 + i * 100, 100, 100)
            pygame.draw.rect(self.screen, col, r)
            self.screen.blit(render_custom(self.ed_pt, self.ed_white, 100), r)

    # ------------------------------------------------------- personalización
    def scr_settings(self):
        self.panel((30, 30, 1040, 680))
        self.text("Personalización", self.f_big, (255, 255, 255), (60, 50))
        rows = [("Fondo", "bg", self.bg_names), ("Casillas", "board", [b[0] for b in BOARDS]),
                ("Piezas", "pieces", PIECE_SKINS)]
        y = 120
        for label, key, opts in rows:
            self.text(label, self.f_mid, (190, 200, 225), (60, y))
            i = opts.index(self.cfg[key]) if self.cfg[key] in opts else 0
            if self.button((60, y + 30, 40, 40), "<"):
                self.cfg[key] = opts[(i - 1) % len(opts)]; self.save_cfg()
            self.text(opts[i], self.f_mid, (255, 255, 255), (280, y + 50), "center")
            if self.button((460, y + 30, 40, 40), ">"):
                self.cfg[key] = opts[(i + 1) % len(opts)]; self.save_cfg()
            y += 95
        if self.cfg["board"] == "Personalizado":
            for ci, (nm, key) in enumerate((("Casilla clara", "light"), ("Casilla oscura", "dark"))):
                self.text(nm, self.f_small, (190, 200, 225), (60, y + ci * 130))
                for j, ch in enumerate("RGB"):
                    v = self.slider((100, y + 32 + ci * 130 + j * 28, 300, 8), self.cfg[key][j], f"{key}{j}")
                    if v != self.cfg[key][j]: self.cfg[key][j] = v; self.save_cfg()
                    self.text(f"{ch} {self.cfg[key][j]}", self.f_small, (255, 255, 255), (60, y + 25 + ci * 130 + j * 28))
                pygame.draw.rect(self.screen, tuple(self.cfg[key]), (430, y + 28 + ci * 130, 60, 70), border_radius=8)
        else:
            if self.button((60, y + 6, 440, 40), f"Girar tablero en 1v1: {'SÍ' if self.cfg['autoflip'] else 'NO'}"):
                self.cfg["autoflip"] = not self.cfg["autoflip"]; self.save_cfg()
            self.text("Tip: pon imágenes .png/.jpg en la carpeta 'fondos'", self.f_small, (170, 180, 205), (60, y + 62))
            self.text("junto al .exe y aparecerán aquí como fondos.", self.f_small, (170, 180, 205), (60, y + 84))
        # vista previa
        self.text("Vista previa", self.f_mid, (190, 200, 225), (560, 60))
        l, d = self.board_colors()
        order = [chess.ROOK, chess.KNIGHT, chess.BISHOP, chess.QUEEN, chess.KING, chess.PAWN]
        for r in range(2):
            for c in range(6):
                rect = pygame.Rect(560 + c * 80, 100 + r * 80, 80, 80)
                pygame.draw.rect(self.screen, l if (r + c) % 2 == 0 else d, rect)
                self.screen.blit(self.sprite(order[c], r == 0), rect)
        for r in range(2):
            for c in range(6):
                rect = pygame.Rect(560 + c * 80, 280 + r * 80, 80, 80)
                pygame.draw.rect(self.screen, l if (r + c) % 2 == 1 else d, rect)
                self.screen.blit(self.sprite(order[(c + 2) % 6], r == 1), rect)
        if "Fortnite" in self.cfg["pieces"]:
            self.text("Pack 'inspirado en Fortnite': diseños originales (pico, llama,", self.f_small, (255, 220, 120), (560, 460))
            self.text("planeador, bus con globo, estrella y corona #1). No usa assets oficiales.", self.f_small, (255, 220, 120), (560, 482))
        if self.button((860, 640, 180, 44), "Volver", accent=True): self.scr = "menu"

    # ---------------------------------------------------------- espejo de IA
    def scr_mirror(self):
        self.panel((60, 40, 980, 660))
        self.text("★ ESPEJO DE IA", self.f_big, (255, 255, 255), (90, 60))
        p = self.profile
        if p:
            self.text(f"Perfil generado con {p['moves']} jugadas tuyas.", self.f_mid, (200, 210, 230), (90, 105))
            bars = [("Agresividad", p["aggression"], f"{int(p['aggression'] * 100)} %"),
                    ("Sacrificios", clamp(p["sac_rate"] / 0.08, 0, 1), f"{p['sac_rate'] * 100:.1f} % de tus jugadas"),
                    ("Capturas", clamp(p["capture_rate"] / 0.35, 0, 1), f"{p['capture_rate'] * 100:.0f} %"),
                    ("Jaques", clamp(p["check_rate"] / 0.12, 0, 1), f"{p['check_rate'] * 100:.0f} %"),
                    ("Velocidad", clamp(1 - p["speed_mean"] / 20, 0, 1), f"{p['speed_mean']:.1f} s por jugada"),
                    ("Precisión", clamp(1 - p["acl"] / 200, 0, 1), f"pérdida media {p['acl']:.0f} cp")]
            for i, (nm, v, txt) in enumerate(bars):
                y = 150 + i * 52
                self.text(nm, self.f_mid, (255, 255, 255), (90, y))
                pygame.draw.rect(self.screen, (70, 74, 92), (250, y + 4, 300, 18), border_radius=9)
                pygame.draw.rect(self.screen, (90, 170, 255), (250, y + 4, int(300 * v), 18), border_radius=9)
                self.text(txt, self.f_small, (200, 210, 230), (565, y + 3))
            self.text("Aperturas favoritas (primeras jugadas):", self.f_mid, (255, 255, 255), (90, 475))
            for i, (ln, c) in enumerate(p["open_lines"] or [("(sin datos)", 0)]):
                self.text(f"• {ln}  (x{c})", self.f_small, (200, 210, 230), (100, 505 + i * 24))
            if self.button((90, 600, 300, 50), "Jugar contra tu Espejo", accent=True):
                self.start_game("mirror")
        else:
            self.text("Aún no tienes un Espejo. Juega partidas de calibración", self.f_mid, (200, 210, 230), (90, 130))
            self.text(f"contra una IA media (mínimo {CALIB_TARGET} jugadas). El sistema analizará", self.f_mid, (200, 210, 230), (90, 158))
            self.text("tu agresividad, sacrificios, velocidad, precisión y aperturas.", self.f_mid, (200, 210, 230), (90, 186))
        n = len(self.calib_records)
        lbl = f"Continuar calibración ({n}/{CALIB_TARGET})" if n else ("Recalibrar" if p else "Iniciar calibración")
        if self.button((420, 600, 300, 50), lbl): self.start_game("calib", "medio")
        if n and self.button((740, 540, 270, 46), "Descartar calibración"): self.calib_records = []
        if self.button((740, 600, 270, 50), "Volver"): self.scr = "menu"
        self.text("El Espejo imita tu estilo de forma aproximada; no es una copia exacta.", self.f_small,
                  (150, 160, 185), (90, 665))

    def scr_analyzing(self):
        self.panel((250, 280, 600, 180))
        self.text("Analizando tu estilo de juego…", self.f_big, (255, 255, 255), (W // 2, 330), "center")
        i, n = self.an_prog
        pygame.draw.rect(self.screen, (70, 74, 92), (300, 380, 500, 22), border_radius=11)
        pygame.draw.rect(self.screen, (90, 170, 255), (300, 380, int(500 * i / max(1, n)), 22), border_radius=11)
        if self.an_done: self.scr = "mirror"

    def start_analysis(self):
        recs = list(self.calib_records)
        self.an_prog, self.an_done, self.scr = (0, 1), False, "analyzing"

        def work():
            prof = build_profile(recs, lambda i, n: setattr(self, "an_prog", (i, n)))
            save_json("espejo.json", prof)
            self.profile = prof; self.calib_records = []; self.an_done = True
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------- partida
    def start_game(self, mode, level=None):
        self.mode, self.level = mode, level
        self.board = chess.Board(); self.sans, self.last, self.sel = [], None, None
        self.promo, self.over_msg, self.ai_result, self.ai_busy = None, None, None, False
        self.game_token += 1
        if mode == "calib":
            self.calib_game += 1
            self.player_white = (self.calib_game % 2 == 1)
        elif mode == "local":
            self.player_white = True
        else:
            self.player_white = random.random() < 0.5 if self.side_choice == 2 else (self.side_choice == 0)
        self.turn_start = time.time()
        self.scr = "game"

    def human_turn(self):
        return self.mode == "local" or self.board.turn == self.player_white

    def flipped(self):
        if self.mode == "local":
            return (not self.board.turn) if self.cfg["autoflip"] else False
        return not self.player_white

    def make_move(self, move, human):
        b = self.board
        if human and self.mode == "calib":
            f = move_features(b, move)
            f.update({"dt": min(60, time.time() - self.turn_start), "fen": b.fen(), "uci": move.uci(),
                      "ply": len(b.move_stack), "san": b.san(move), "game": self.calib_game})
            self.calib_records.append(f)
        self.sans.append(b.san(move))
        b.push(move)
        self.last = (move.from_square, move.to_square)
        out = b.outcome(claim_draw=True)
        if out:
            if out.winner is None: self.over_msg = "Tablas"
            else: self.over_msg = "Jaque mate: ganan " + ("Blancas" if out.winner else "Negras")
        self.turn_start = time.time()

    def start_ai(self):
        self.ai_busy, self.ai_result = True, None
        tok, board, lvl, mode, prof = self.game_token, self.board.copy(), self.level, self.mode, self.profile

        def work():
            t0 = time.time()
            try:
                if mode == "mirror":
                    mv = mirror_move(board, prof)
                    delay = clamp(random.choice(prof["times"]) * random.uniform(0.8, 1.2), 0.4, 12)
                else:
                    mv, delay = ai_choose(board, lvl or "medio"), 0.5
            except Exception:
                mv, delay = random.choice(list(board.legal_moves)), 0
            if delay > time.time() - t0: time.sleep(delay - (time.time() - t0))
            if tok == self.game_token:
                self.ai_result = mv; self.ai_busy = False
        threading.Thread(target=work, daemon=True).start()

    def pos_to_square(self, pos):
        f, r = (pos[0] - BX) // SQ, (pos[1] - BY) // SQ
        if not (0 <= f < 8 and 0 <= r < 8): return None
        fl = self.flipped()
        return chess.square(7 - f if fl else f, r if fl else 7 - r)

    def board_click(self):
        sq = self.pos_to_square(self.click)
        if sq is None: return
        b = self.board
        if self.sel is not None:
            cands = [m for m in b.legal_moves if m.from_square == self.sel and m.to_square == sq]
            if cands:
                if len(cands) > 1: self.promo = (self.sel, sq)
                else: self.sel = None; self.make_move(cands[0], True)
                return
        p = b.piece_at(sq)
        self.sel = sq if (p and p.color == b.turn) else None

    def draw_board(self):
        b, fl = self.board, self.flipped()
        l, d = self.board_colors()
        targets = {m.to_square for m in b.legal_moves if self.sel is not None and m.from_square == self.sel}
        king_chk = b.king(b.turn) if b.is_check() else None
        ov = pygame.Surface((SQ, SQ), pygame.SRCALPHA)
        for r in range(8):
            for c in range(8):
                file, rank = (7 - c, r) if fl else (c, 7 - r)
                s = chess.square(file, rank)
                rect = pygame.Rect(BX + c * SQ, BY + r * SQ, SQ, SQ)
                pygame.draw.rect(self.screen, l if (file + rank) % 2 == 1 else d, rect)
                tint = None
                if self.last and s in self.last: tint = (255, 230, 60, 95)
                if s == self.sel: tint = (60, 220, 120, 120)
                if s == king_chk: tint = (255, 40, 40, 150)
                if tint: ov.fill(tint); self.screen.blit(ov, rect)
                p = b.piece_at(s)
                if p: self.screen.blit(self.sprite(p.piece_type, p.color), rect)
                if s in targets:
                    if p: pygame.draw.circle(self.screen, (40, 40, 40), rect.center, SQ // 2 - 3, 5)
                    else:
                        dot = pygame.Surface((SQ, SQ), pygame.SRCALPHA)
                        pygame.draw.circle(dot, (30, 30, 30, 120), (SQ // 2, SQ // 2), 11)
                        self.screen.blit(dot, rect)
                if c == 0: self.text(str(rank + 1), self.f_small, (230, 230, 230), (BX - 14, rect.y + 28))
                if r == 7: self.text("abcdefgh"[file], self.f_small, (230, 230, 230), (rect.x + 34, BY + 8 * SQ + 4))

    def captured_row(self, y, by_white):
        start = {chess.PAWN: 8, chess.KNIGHT: 2, chess.BISHOP: 2, chess.ROOK: 2, chess.QUEEN: 1}
        x = 730
        for pt, n in start.items():
            have = len(self.board.pieces(pt, not by_white))
            for _ in range(max(0, n - have)):
                self.screen.blit(self.sprite(pt, not by_white, 30), (x, y)); x += 24

    def scr_game(self):
        b = self.board
        # --- turno de la IA
        if not self.over_msg and not self.human_turn():
            if not self.ai_busy and self.ai_result is None: self.start_ai()
            if not self.ai_busy and self.ai_result is not None:
                mv, self.ai_result = self.ai_result, None
                if mv in b.legal_moves: self.make_move(mv, False)
        # --- clics
        if self.click and not self.over_msg and self.human_turn() and not self.promo:
            if BX <= self.click[0] < BX + 8 * SQ and BY <= self.click[1] < BY + 8 * SQ:
                self.board_click()
        self.draw_board()
        if self.promo:
            self.panel((BX + 120, BY + 250, 400, 140), 235)
            self.text("Coronar a:", self.f_mid, (255, 255, 255), (BX + 320, BY + 262), "center")
            for i, pt in enumerate((chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT)):
                r = pygame.Rect(BX + 135 + i * 95, BY + 285, 85, 85)
                pygame.draw.rect(self.screen, (60, 70, 100), r, border_radius=10)
                self.screen.blit(self.sprite(pt, b.turn), (r.x + 2, r.y + 2))
                if self.click and r.collidepoint(self.click):
                    mv = chess.Move(self.promo[0], self.promo[1], promotion=pt)
                    self.promo, self.sel, self.click = None, None, None
                    self.make_move(mv, True)
        # --- panel lateral
        self.panel((700, 40, 370, 660))
        titles = {"local": "1 vs 1 local", "calib": f"Calibración · partida {self.calib_game}",
                  "mirror": "Contra tu Espejo",
                  "ai": "Contra la IA · " + {"facil": "Fácil", "medio": "Medio", "dificil": "Difícil"}.get(self.level, "")}
        self.text(titles[self.mode], self.f_big, (255, 255, 255), (720, 55))
        turn = "Blancas" if b.turn else "Negras"
        status = self.over_msg or (f"Turno: {turn}" + ("  (tú)" if self.mode != "local" and self.human_turn() else ""))
        if not self.over_msg and not self.human_turn(): status = f"Piensa… ({turn})"
        self.text(status, self.f_mid, (255, 225, 120), (720, 95))
        self.captured_row(130, True); self.captured_row(160, False)
        # lista de jugadas
        pairs = [self.sans[i:i + 2] for i in range(0, len(self.sans), 2)][-12:]
        first = max(0, (len(self.sans) + 1) // 2 - len(pairs)) + 1
        for i, pr in enumerate(pairs):
            self.text(f"{first + i}. {pr[0]:<8} {pr[1] if len(pr) > 1 else ''}", self.f_mid, (220, 225, 240), (730, 205 + i * 26))
        # botones
        y = 540
        can_undo = self.mode in ("local", "ai", "mirror") and len(b.move_stack) >= (1 if self.mode == "local" else 2) and not self.ai_busy
        if self.button((720, y, 160, 42), "Deshacer", enabled=can_undo, font=self.f_small):
            n = 1 if self.mode == "local" else 2
            for _ in range(n):
                if b.move_stack: b.pop(); self.sans.pop()
            self.over_msg, self.sel, self.last, self.ai_result = None, None, (b.peek().from_square, b.peek().to_square) if b.move_stack else None, None
            self.game_token += 1; self.ai_busy = False
        if self.button((890, y, 160, 42), "Nueva partida", enabled=self.mode != "calib", font=self.f_small):
            self.start_game(self.mode, self.level)
        if self.mode == "calib":
            n = len(self.calib_records)
            self.text(f"Jugadas registradas: {n}/{CALIB_TARGET}", self.f_small, (200, 210, 230), (720, 598))
            pygame.draw.rect(self.screen, (70, 74, 92), (720, 620, 330, 12), border_radius=6)
            pygame.draw.rect(self.screen, (90, 170, 255), (720, 620, int(330 * min(1, n / CALIB_TARGET)), 12), border_radius=6)
            if self.button((720, 644, 330, 42), "Finalizar y generar mi Espejo", enabled=n >= CALIB_TARGET, accent=True, font=self.f_small):
                self.start_analysis(); return
        elif self.button((720, 600, 330, 42), "Menú principal", font=self.f_small):
            self.scr = "menu"; self.game_token += 1; return
        if self.over_msg:
            self.panel((BX + 90, BY + 240, 460, 160), 240)
            self.text(self.over_msg, self.f_big, (255, 255, 255), (BX + 320, BY + 275), "center")
            nxt = "Siguiente partida" if self.mode == "calib" else "Revancha"
            if self.button((BX + 110, BY + 320, 200, 50), nxt, accent=True):
                self.start_game(self.mode, self.level)
            if self.button((BX + 330, BY + 320, 200, 50), "Menú" if self.mode != "calib" else "Espejo"):
                self.scr = "menu" if self.mode != "calib" else "mirror"; self.game_token += 1


if __name__ == "__main__":
    App().run()

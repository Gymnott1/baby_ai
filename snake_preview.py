# ── HEADER ──

"""
Snake — built autonomously by ARIA
Generated: 2026-06-07 19:43
"""

# ── IMPORTS ──

import pygame, sys, random, time
from pathlib import Path

# ── CONSTANTS ──

COLS, ROWS = 20, 20
CELL       = 28
WIDTH      = COLS * CELL
HEIGHT     = ROWS * CELL + 60      # extra bar for score
FPS        = 10

# ── COLORS ──

BG         = (15,  17,  21)
GRID       = (25,  28,  35)
SNAKE_HEAD = (80,  220, 160)
SNAKE_BODY = (40,  160, 100)
FOOD       = (220, 80,  80)
FOOD_SHINE = (240, 140, 140)
TEXT       = (200, 200, 200)
ACCENT     = (80,  220, 160)
DEAD       = (180, 60,  60)

# ── SNAKE_CLASS ──

class Snake:
    def __init__(self):
        self.reset()

    def reset(self):
        cx, cy       = COLS // 2, ROWS // 2
        self.body    = [(cx, cy), (cx-1, cy), (cx-2, cy)]
        self.dir     = (1, 0)
        self.grew    = False
        self.alive   = True

    def change_dir(self, new_dir):
        # Prevent reversing
        if (new_dir[0] * -1, new_dir[1] * -1) != self.dir:
            self.dir = new_dir

    def move(self):
        hx, hy  = self.body[0]
        dx, dy  = self.dir
        new_head = (hx + dx, hy + dy)

        # Wall collision
        if not (0 <= new_head[0] < COLS and 0 <= new_head[1] < ROWS):
            self.alive = False
            return

        # Self collision
        if new_head in self.body[:-1]:
            self.alive = False
            return

        self.body.insert(0, new_head)
        if not self.grew:
            self.body.pop()
        self.grew = False

    def grow(self):
        self.grew = True

    def draw(self, surf):
        for i, (x, y) in enumerate(self.body):
            rect  = pygame.Rect(x*CELL+1, y*CELL+1, CELL-2, CELL-2)
            color = SNAKE_HEAD if i == 0 else SNAKE_BODY
            pygame.draw.rect(surf, color, rect, border_radius=5)
            if i == 0:
                # Eyes
                ex = x*CELL + (CELL//2) + self.dir[0]*4
                ey = y*CELL + (CELL//2) + self.dir[1]*4
                offset = (self.dir[1]*5, self.dir[0]*5)
                pygame.draw.circle(surf, BG, (ex+offset[0], ey+offset[1]), 3)
                pygame.draw.circle(surf, BG, (ex-offset[0], ey-offset[1]), 3)

# ── FOOD_CLASS ──

class Food:
    def __init__(self, snake):
        self.pos   = (0, 0)
        self.anim  = 0
        self.spawn(snake)

    def spawn(self, snake):
        empty = [(x, y) for x in range(COLS) for y in range(ROWS)
                 if (x, y) not in snake.body]
        self.pos = random.choice(empty) if empty else (0, 0)

    def draw(self, surf):
        self.anim = (self.anim + 1) % 30
        pulse     = abs(self.anim - 15) / 15
        x, y      = self.pos
        size      = int(CELL * 0.38 + pulse * 3)
        cx        = x*CELL + CELL//2
        cy        = y*CELL + CELL//2
        pygame.draw.circle(surf, FOOD,       (cx, cy),   size)
        pygame.draw.circle(surf, FOOD_SHINE, (cx-2, cy-2), max(2, size//3))

# ── GAME_CLASS ──

class Game:
    def __init__(self):
        pygame.init()
        self.screen  = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("Snake  ·  built by ARIA")
        self.clock   = pygame.time.Clock()
        self.font_lg = pygame.font.SysFont("monospace", 28, bold=True)
        self.font_sm = pygame.font.SysFont("monospace", 16)
        self.font_xs = pygame.font.SysFont("monospace", 13)
        self.reset()

    def reset(self):
        self.snake    = Snake()
        self.food     = Food(self.snake)
        self.score    = 0
        self.hi_score = getattr(self, 'hi_score', 0)
        self.state    = 'playing'   # playing | dead
        self.tick     = 0

    def handle_events(self):
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if self.state == 'dead':
                    if ev.key == pygame.K_SPACE:
                        self.reset()
                    return
                key_map = {
                    pygame.K_UP:    (0,-1), pygame.K_w: (0,-1),
                    pygame.K_DOWN:  (0, 1), pygame.K_s: (0, 1),
                    pygame.K_LEFT:  (-1,0), pygame.K_a: (-1,0),
                    pygame.K_RIGHT: (1, 0), pygame.K_d: (1, 0),
                }
                if ev.key in key_map:
                    self.snake.change_dir(key_map[ev.key])

    def update(self):
        if self.state != 'playing':
            return
        self.tick += 1
        self.snake.move()
        if not self.snake.alive:
            self.state = 'dead'
            if self.score > self.hi_score:
                self.hi_score = self.score
            return
        if self.snake.body[0] == self.food.pos:
            self.snake.grow()
            self.score += 10
            self.food.spawn(self.snake)

    def draw_grid(self):
        for x in range(COLS):
            for y in range(ROWS):
                r = pygame.Rect(x*CELL, y*CELL, CELL, CELL)
                pygame.draw.rect(self.screen, GRID, r, 1)

    def draw_hud(self):
        bar_y = ROWS * CELL
        pygame.draw.rect(self.screen, (20,22,28), (0, bar_y, WIDTH, 60))
        pygame.draw.line(self.screen, ACCENT, (0, bar_y), (WIDTH, bar_y), 1)

        sc  = self.font_sm.render(f"SCORE  {self.score:04d}", True, TEXT)
        hi  = self.font_sm.render(f"BEST   {self.hi_score:04d}", True, ACCENT)
        tip = self.font_xs.render("WASD / ARROWS · built by ARIA", True, (70,75,90))
        self.screen.blit(sc,  (16, bar_y + 8))
        self.screen.blit(hi,  (16, bar_y + 28))
        self.screen.blit(tip, (WIDTH - tip.get_width() - 10, bar_y + 22))

    def draw_dead(self):
        overlay = pygame.Surface((WIDTH, ROWS*CELL), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        self.screen.blit(overlay, (0, 0))

        t1 = self.font_lg.render("GAME OVER", True, DEAD)
        t2 = self.font_sm.render(f"Score: {self.score}   Best: {self.hi_score}", True, TEXT)
        t3 = self.font_sm.render("SPACE to play again", True, ACCENT)
        cx = WIDTH // 2
        cy = (ROWS * CELL) // 2
        self.screen.blit(t1, t1.get_rect(center=(cx, cy-30)))
        self.screen.blit(t2, t2.get_rect(center=(cx, cy+10)))
        self.screen.blit(t3, t3.get_rect(center=(cx, cy+40)))

    def run(self):
        while True:
            self.handle_events()
            self.update()
            self.screen.fill(BG)
            self.draw_grid()
            self.food.draw(self.screen)
            self.snake.draw(self.screen)
            self.draw_hud()
            if self.state == 'dead':
                self.draw_dead()
            pygame.display.flip()
            self.clock.tick(FPS)

# ── DRAW_FUNCTIONS ──

def show_launch_screen(screen, font_lg, font_sm, font_xs):
    """
    ARIA's completion message — shown before the game starts.
    """
    screen.fill(BG)
    t1 = font_lg.render("✓  ARIA COMPLETE", True, SNAKE_HEAD)
    t2 = font_sm.render("Snake game built autonomously.", True, TEXT)
    t3 = font_sm.render("Check out this game!", True, ACCENT)
    t4 = font_xs.render("Press SPACE to play  ·  ESC to quit", True, (90,95,110))

    cx = WIDTH // 2
    cy = HEIGHT // 2
    screen.blit(t1, t1.get_rect(center=(cx, cy-60)))
    screen.blit(t2, t2.get_rect(center=(cx, cy-15)))
    screen.blit(t3, t3.get_rect(center=(cx, cy+20)))
    screen.blit(t4, t4.get_rect(center=(cx, cy+70)))

    # Decorative border
    pygame.draw.rect(screen, SNAKE_HEAD,
                     (WIDTH//2-180, cy-90, 360, 200), 1, border_radius=8)
    pygame.display.flip()

# ── MAIN_LOOP ──

def wait_for_launch(screen, font_lg, font_sm, font_xs):
    """Show launch screen; wait for SPACE or ESC."""
    clock = pygame.time.Clock()
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_SPACE:
                    return True
                if ev.key == pygame.K_ESCAPE:
                    return False
        show_launch_screen(screen, font_lg, font_sm, font_xs)
        clock.tick(30)

# ── ENTRY ──

if __name__ == "__main__":
    pygame.init()
    screen  = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Snake  ·  built by ARIA")
    font_lg = pygame.font.SysFont("monospace", 28, bold=True)
    font_sm = pygame.font.SysFont("monospace", 16)
    font_xs = pygame.font.SysFont("monospace", 13)

    # Show ARIA's launch message first
    go = wait_for_launch(screen, font_lg, font_sm, font_xs)
    if go:
        game = Game()
        game.screen = screen   # reuse the same window
        game.run()
    pygame.quit()
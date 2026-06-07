import pygame
import random

# Constants
SCREEN_WIDTH, SCREEN_HEIGHT = 300, 600
GRID_SIZE = 30
COLUMNS, ROWS = SCREEN_WIDTH // GRID_SIZE, SCREEN_HEIGHT // GRID_SIZE
FPS = 60

# Tetromino shapes
SHAPES = [
    [[1, 1, 1, 1]],  # I
    [[1, 1], [1, 1]],  # O
    [[0, 1, 0], [1, 1, 1]],  # T
    [[1, 1, 0], [0, 1, 1]],  # Z
    [[0, 1, 1], [1, 1, 0]],  # S
    [[1, 0, 0], [1, 1, 1]],  # L
    [[0, 0, 1], [1, 1, 1]]   # J
]

# Colors
COLORS = [
    (0, 255, 255),  # Cyan
    (255, 255, 0),  # Yellow
    (128, 0, 128),  # Purple
    (255, 0, 0),    # Red
    (0, 255, 0),    # Green
    (255, 165, 0),  # Orange
    (0, 0, 255)     # Blue
]

class Tetromino:
    def __init__(self, shape, color):
        self.shape = shape
        self.color = color
        self.x = COLUMNS // 2 - len(shape[0]) // 2
        self.y = 0

    def rotate(self):
        self.shape = [list(row) for row in zip(*self.shape[::-1])]

    def move(self, dx, dy):
        self.x += dx
        self.y += dy

class Board:
    def __init__(self):
        self.grid = [[(0, 0, 0) for _ in range(COLUMNS)] for _ in range(ROWS)]

    def is_valid_position(self, tetromino, offset_x=0, offset_y=0):
        for y, row in enumerate(tetromino.shape):
            for x, cell in enumerate(row):
                if cell:
                    new_x = tetromino.x + x + offset_x
                    new_y = tetromino.y + y + offset_y
                    if new_x < 0 or new_x >= COLUMNS or new_y >= ROWS or (new_y >= 0 and self.grid[new_y][new_x] != (0, 0, 0)):
                        return False
        return True

    def place_tetromino(self, tetromino):
        for y, row in enumerate(tetromino.shape):
            for x, cell in enumerate(row):
                if cell:
                    self.grid[tetromino.y + y][tetromino.x + x] = tetromino.color

    def clear_lines(self):
        new_grid = []
        cleared_lines = 0
        for row in self.grid:
            if all(cell != (0, 0, 0) for cell in row):
                cleared_lines += 1
            else:
                new_grid.append(row)
        for _ in range(cleared_lines):
            new_grid.insert(0, [(0, 0, 0) for _ in range(COLUMNS)])
        self.grid = new_grid
        return cleared_lines

class Game:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("Tetris")
        self.clock = pygame.time.Clock()
        self.board = Board()
        self.current_piece = self.new_tetromino()
        self.game_over = False
        self.drop_time = 0
        self.score = 0
        self.level = 1
        self.lines_cleared = 0
        self.move_down_time = 0

    def new_tetromino(self):
        shape = random.choice(SHAPES)
        color = random.choice(COLORS)
        return Tetromino(shape, color)

    def handle_input(self):
        keys = pygame.key.get_pressed()
        if keys[pygame.K_LEFT]:
            if self.board.is_valid_position(self.current_piece, offset_x=-1):
                self.current_piece.move(-1, 0)
        if keys[pygame.K_RIGHT]:
            if self.board.is_valid_position(self.current_piece, offset_x=1):
                self.current_piece.move(1, 0)
        if keys[pygame.K_DOWN]:
            self.move_down_time += self.clock.get_time()
            if self.move_down_time > 50:
                self.move_down_time = 0
                if self.board.is_valid_position(self.current_piece, offset_y=1):
                    self.current_piece.move(0, 1)
        if keys[pygame.K_UP]:
            self.current_piece.rotate()
            if not self.board.is_valid_position(self.current_piece):
                self.current_piece.rotate()
                self.current_piece.rotate()
                self.current_piece.rotate()
        if keys[pygame.K_SPACE]:
            while self.board.is_valid_position(self.current_piece, offset_y=1):
                self.current_piece.move(0, 1)

    def update(self):
        self.drop_time += self.clock.get_time()
        if self.drop_time > 500:
            self.drop_time = 0
            if self.board.is_valid_position(self.current_piece, offset_y=1):
                self.current_piece.move(0, 1)
            else:
                self.board.place_tetromino(self.current_piece)
                cleared_lines = self.board.clear_lines()
                self.score += self.calculate_score(cleared_lines)
                self.lines_cleared += cleared_lines
                if self.lines_cleared >= 10:
                    self.level += 1
                    self.lines_cleared = 0
                self.current_piece = self.new_tetromino()
                if not self.board.is_valid_position(self.current_piece):
                    self.game_over = True

    def calculate_score(self, cleared_lines):
        if cleared_lines == 1:
            return 100
        elif cleared_lines == 2:
            return 300
        elif cleared_lines == 3:
            return 500
        elif cleared_lines == 4:
            return 800
        else:
            return 0

    def draw(self):
        self.screen.fill((0, 0, 0))
        for y, row in enumerate(self.board.grid):
            for x, cell in enumerate(row):
                if cell != (0, 0, 0):
                    pygame.draw.rect(self.screen, cell, (x * GRID_SIZE, y * GRID_SIZE, GRID_SIZE, GRID_SIZE))
        for y, row in enumerate(self.current_piece.shape):
            for x, cell in enumerate(row):
                if cell:
                    pygame.draw.rect(self.screen, self.current_piece.color, ((self.current_piece.x + x) * GRID_SIZE, (self.current_piece.y + y) * GRID_SIZE, GRID_SIZE, GRID_SIZE))
        font = pygame.font.Font(None, 36)
        text = font.render(f"Score: {self.score}", True, (255, 255, 255))
        self.screen.blit(text, (10, 10))
        text = font.render(f"Level: {self.level}", True, (255, 255, 255))
        self.screen.blit(text, (10, 50))
        text = font.render(f"Lines Cleared: {self.lines_cleared}", True, (255, 255, 255))
        self.screen.blit(text, (10, 90))
        pygame.draw.rect(self.screen, (255, 255, 255), (0, 0, SCREEN_WIDTH, GRID_SIZE), 1)
        for x in range(COLUMNS):
            pygame.draw.line(self.screen, (255, 255, 255), (x * GRID_SIZE, 0), (x * GRID_SIZE, SCREEN_HEIGHT), 1)
        for y in range(ROWS):
            pygame.draw.line(self.screen, (255, 255, 255), (0, y * GRID_SIZE), (SCREEN_WIDTH, y * GRID_SIZE), 1)
        pygame.display.flip()

    def run(self):
        while not self.game_over:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.game_over = True
            self.handle_input()
            self.update()
            self.draw()
            self.clock.tick(FPS)
        pygame.quit()

if __name__ == "__main__":
    game = Game()
    game.run()
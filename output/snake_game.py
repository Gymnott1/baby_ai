import pygame
import sys
import random

def code_game_loop():
    pygame.init()

    # Screen dimensions
    WIDTH, HEIGHT = 800, 600
    CELL_SIZE = 20

    # Colors
    BLACK = (0, 0, 0)
    WHITE = (255, 255, 255)
    GREEN = (0, 255, 0)
    RED = (255, 0, 0)

    # Initialize screen
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Snake Game")

    # Font for score display
    font = pygame.font.Font(None, 36)

    # Clock for controlling frame rate
    clock = pygame.time.Clock()
    FPS = 10

    # Snake initialization
    snake = [(WIDTH // 2, HEIGHT // 2)]
    direction = (CELL_SIZE, 0)  # Initial direction: moving right

    # Food initialization
    food = (random.randint(0, (WIDTH - CELL_SIZE) // CELL_SIZE) * CELL_SIZE,
            random.randint(0, (HEIGHT - CELL_SIZE) // CELL_SIZE) * CELL_SIZE)

    # Score initialization
    score = 0

    def move_snake(snake, direction):
        head_x, head_y = snake[0]
        new_head = (head_x + direction[0], head_y + direction[1])
        snake = [new_head] + snake[:-1]
        return snake

    def spawn_food():
        return (random.randint(0, (WIDTH - CELL_SIZE) // CELL_SIZE) * CELL_SIZE,
                random.randint(0, (HEIGHT - CELL_SIZE) // CELL_SIZE) * CELL_SIZE)

    def display_score(score):
        score_text = font.render(f"Score: {score}", True, WHITE)
        screen.blit(score_text, (10, 10))

    # Main game loop
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_UP and direction != (0, CELL_SIZE):
                    direction = (0, -CELL_SIZE)
                elif event.key == pygame.K_DOWN and direction != (0, -CELL_SIZE):
                    direction = (0, CELL_SIZE)
                elif event.key == pygame.K_LEFT and direction != (CELL_SIZE, 0):
                    direction = (-CELL_SIZE, 0)
                elif event.key == pygame.K_RIGHT and direction != (-CELL_SIZE, 0):
                    direction = (CELL_SIZE, 0)

        # Update game state
        snake = move_snake(snake, direction)

        # Check for food collision
        if snake[0] == food:
            snake.append(snake[-1])  # Grow the snake
            food = spawn_food()  # Spawn new food
            score += 1  # Increase score

        # Clear screen
        screen.fill(BLACK)

        # Render snake
        for segment in snake:
            pygame.draw.rect(screen, GREEN, (segment[0], segment[1], CELL_SIZE, CELL_SIZE))

        # Render food
        pygame.draw.rect(screen, RED, (food[0], food[1], CELL_SIZE, CELL_SIZE))

        # Display score
        display_score(score)

        # Update display
        pygame.display.flip()

        # Control frame rate
        clock.tick(FPS)

    pygame.quit()
    sys.exit()

code_game_loop()
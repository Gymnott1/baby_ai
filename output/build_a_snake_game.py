import pygame
import sys
import time
import random

pygame.init()

white = (255, 255, 255)
black = (0, 0, 0)
red = (255, 0, 0)
green = (0, 255, 0)
blue = (0, 0, 255)

width, height = 800, 600
score = 0
direction = 'right'

display = pygame.display.set_mode((width, height))
pygame.display.set_caption('Snake Game')
clock = pygame.time.Clock()

font = pygame.font.Font(None, 36)

snake_pos = [100, 50]
snake_body = [[100, 50], [90, 50], [80, 50], [70, 50]]
food_pos = [random.randrange(1, (width//10)) * 10, random.randrange(1, (height//10)) * 10]

while True:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_UP and direction != 'down':
                direction = 'up'
            elif event.key == pygame.K_DOWN and direction != 'up':
                direction = 'down'
            elif event.key == pygame.K_LEFT and direction != 'right':
                direction = 'left'
            elif event.key == pygame.K_RIGHT and direction != 'left':
                direction = 'right'

    if direction == 'up':
        snake_pos[1] -= 10
    elif direction == 'down':
        snake_pos[1] += 10
    elif direction == 'left':
        snake_pos[0] -= 10
    elif direction == 'right':
        snake_pos[0] += 10

    snake_body.insert(0, list(snake_pos))
    if snake_pos == food_pos:
        score += 1
        food_pos = [random.randrange(1, (width//10)) * 10, random.randrange(1, (height//10)) * 10]
    else:
        snake_body.pop()

    if (snake_pos[0] < 0 or snake_pos[0] > width-10) or (snake_pos[1] < 0 or snake_pos[1] > height-10):
        print('Game Over')
        print('Your score is:', score)
        pygame.quit()
        sys.exit()
    for block in snake_body[1:]:
        if snake_pos == block:
            print('Game Over')
            print('Your score is:', score)
            pygame.quit()
            sys.exit()

    display.fill(black)
    for pos in snake_body:
        pygame.draw.rect(display, green, pygame.Rect(pos[0], pos[1], 10, 10))
    pygame.draw.rect(display, white, pygame.Rect(food_pos[0], food_pos[1], 10, 10))

    text = font.render('Score: ' + str(score), True, white)
    display.blit(text, [0, 0])

    pygame.display.update()
    clock.tick(10)
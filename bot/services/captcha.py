import random


def generate_captcha() -> tuple[str, int]:
    a = random.randint(5, 20)
    b = random.randint(1, 10)
    op = random.choice(["+", "-"])
    if op == "+":
        answer = a + b
        question = f"{a} + {b}"
    else:
        if a < b:
            a, b = b, a
        answer = a - b
        question = f"{a} - {b}"
    return question, answer

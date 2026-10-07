"""Small replaceable exam bank; answers stay on the server."""
import random

BANK = [
    {"id": "q1", "text": "Какое свойство транзакции означает «всё или ничего»?", "options": ["Атомарность", "Изолированность", "Долговечность", "Доступность"], "correct": 0},
    {"id": "q2", "text": "Какой SQL-оператор выбирает строки из таблицы?", "options": ["UPDATE", "SELECT", "INSERT", "CREATE"], "correct": 1},
    {"id": "q3", "text": "Что делает первичный ключ в реляционной базе данных?", "options": ["Шифрует таблицу", "Сортирует столбцы", "Однозначно определяет запись", "Создаёт резервную копию"], "correct": 2},
    {"id": "q4", "text": "Какой протокол обеспечивает защищённое соединение с веб-сайтом?", "options": ["FTP", "HTTP", "UDP", "HTTPS"], "correct": 3},
    {"id": "q5", "text": "Как называется обучение на примерах с известными правильными ответами?", "options": ["Обучение с учителем", "Кластеризация", "Обучение без учителя", "Случайный поиск"], "correct": 0},
    {"id": "q6", "text": "Для чего обычно используют индекс базы данных?", "options": ["Для удаления дубликатов", "Для ускорения поиска", "Для шифрования паролей", "Для замены резервного копирования"], "correct": 1},
    {"id": "q7", "text": "Что из перечисленного является системой контроля версий?", "options": ["Docker", "PostgreSQL", "Git", "Nginx"], "correct": 2},
    {"id": "q8", "text": "Какой принцип означает выдачу только необходимых пользователю прав?", "options": ["Открытый доступ", "Общая учётная запись", "Полный контроль", "Минимальные привилегии"], "correct": 3},
]


def make_exam(seed, bank=None):
    rng = random.Random(seed)
    result = []
    for q in BANK if bank is None else bank:
        indices = list(range(len(q["options"])))
        rng.shuffle(indices)
        result.append({"id": q["id"], "text": q["text"],
                       "options": [q["options"][i] for i in indices],
                       "correct": indices.index(q["correct"])})
    rng.shuffle(result)
    return result


def public_exam(exam):
    return [{k: v for k, v in q.items() if k != "correct"} for q in exam]


def grade(exam, answers):
    correct = sum(answers.get(q["id"]) == q["correct"] for q in exam)
    return {"correct": correct, "total": len(exam), "percent": round(100 * correct / len(exam))}

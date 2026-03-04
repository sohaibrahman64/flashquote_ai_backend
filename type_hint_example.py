from datetime import datetime
from typing import List

from pydantic import BaseModel
from typing import Annotated


def get_full_name(first_name: str, last_name: str) -> str:
    full_name = first_name.title() + " " + last_name.title()
    return full_name

print(get_full_name("John", "Doe"))


def get_items(item_a: str, item_b: int, item_c: float, item_d: bool, item_e: bytes) -> tuple[str, int, float, bool, bytes]:
    return item_a, item_b, item_c, item_d, item_e

def process_items(items: list[str]):
    for item in items:
        print(item)

def process_items_1(items: List[str]):
    for item in items:
        print(item)

def process_items_2(items_t: tuple[int, int, str], items_s: set[bytes]):
    return items_t, items_s


def process_items_3(prices: dict[str, float]):
    for item, price in prices.items():
        print(f"{item}: ${price:.2f}")

def process_items_4(item: int | str):
    print(item)

def say_hi(name: str | None = None):
    if name is not None:
        print(f"Hi, {name}!")
    else:
        print("Hi!")

class Person:
    def __init__(self, name: str):
        self.name = name

    def get_person_name(one_person: Person):
        return one_person.name

class User(BaseModel):
    id: int
    name: str = "John Doe"
    signup_ts: datetime| None = None
    friends: list[int] = []

external_data = {
    "id": "123",
    "signup_ts": "2023-01-01T00:00:00",
    "friends": [1, "2", b"3"]
}
user = User(**external_data)
print(user)

def say_hello(name: Annotated[str, "this is just metadata"]) -> str:
    return f"Hello, {name}!"
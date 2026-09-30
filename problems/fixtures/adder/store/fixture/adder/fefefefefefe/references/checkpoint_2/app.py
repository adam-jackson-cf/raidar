import os
import sys


def main(argv):
    op, *raw = argv
    try:
        a, b = (int(value) for value in raw)
    except ValueError:
        return 2

    if op == "add":
        print(a + b)
        return 0

    if op == "mul":
        print(a * b)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

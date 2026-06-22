#!/usr/bin/env python3
"""
Fibonacci Sequence Calculator
This script prints the Fibonacci sequence up to a specified number of terms.
"""

def fibonacci(n: int) -> list[int]:
    """
    Calculate the Fibonacci sequence up to n terms.
    
    Args:
        n: Number of terms to generate (must be >= 0)
    
    Returns:
        List containing the Fibonacci sequence
    
    Example:
        >>> fibonacci(7)
        [0, 1, 1, 2, 3, 5, 8]
    """
    if n <= 0:
        return []
    elif n == 1:
        return [0]
    elif n == 2:
        return [0, 1]
    
    sequence = [0, 1]
    for i in range(2, n):
        sequence.append(sequence[i-1] + sequence[i-2])
    
    return sequence


def fibonacci_recursive(n: int) -> int:
    """
    Calculate the nth Fibonacci number recursively.
    
    Args:
        n: Position in the sequence (0-indexed)
    
    Returns:
        The nth Fibonacci number
    """
    if n <= 0:
        return 0
    elif n == 1:
        return 1
    else:
        return fibonacci_recursive(n-1) + fibonacci_recursive(n-2)


def main():
    print("=" * 50)
    print("FIBONACCI SEQUENCE CALCULATOR")
    print("=" * 50)
    
    try:
        n = int(input("Enter the number of terms: "))
        if n < 0:
            print("Please enter a non-negative number.")
            return
        
        seq = fibonacci(n)
        
        print(f"\nFibonacci sequence ({n} terms):")
        print("-" * 40)
        
        for i, num in enumerate(seq):
            print(f"F({i:3d}) = {num:10d}")
        
        print("-" * 40)
        print(f"\nThe {n}th Fibonacci number (0-indexed) is: {fibonacci_recursive(n-1) if n > 0 else 'N/A'}")
        print(f"Sum of all terms: {sum(seq)}")
        
    except ValueError:
        print("Invalid input. Please enter an integer.")
    except KeyboardInterrupt:
        print("\n\nProgram terminated by user.")


if __name__ == "__main__":
    main()

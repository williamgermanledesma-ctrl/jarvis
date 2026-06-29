"""
main.py
-------
The orchestrator. This is what you run.

Flow:
  1. You type a request.
  2. The local model (via Ollama) decides whether to call a tool.
  3. If the tool is marked "destructive", you must approve it first.
  4. The tool result is fed back to the model.
  5. The model gives you a final natural-language answer.

Run with:  python main.py
Quit with: type 'quit' or press Ctrl+C
"""

import ollama
from registry import SCHEMAS, FUNCTIONS, DESTRUCTIVE

MODEL = "llama3.1:8b"   # change this if you pulled a different model


def handle_turn(user_input, history):
    history.append({"role": "user", "content": user_input})

    # First call: let the model decide if it wants a tool
    response = ollama.chat(model=MODEL, messages=history, tools=SCHEMAS)
    msg = response["message"]
    history.append(msg)

    tool_calls = msg.get("tool_calls")

    # No tool needed - just a normal answer
    if not tool_calls:
        print(f"\nAssistant: {msg['content']}\n")
        return

    # The model wants to use one or more tools
    for call in tool_calls:
        name = call["function"]["name"]
        args = call["function"]["arguments"]

        if name not in FUNCTIONS:
            history.append({"role": "tool", "name": name,
                            "content": f"Unknown tool: {name}"})
            continue

        # ---------- HUMAN-IN-THE-LOOP GATE ----------
        if DESTRUCTIVE.get(name, False):
            print(f"\n  ⚠️  The assistant wants to run a DESTRUCTIVE action:")
            print(f"      {name}({args})")
            approve = input("      Approve? (y/n): ").strip().lower()
            if approve != "y":
                history.append({"role": "tool", "name": name,
                                "content": "User denied this action."})
                print("      Skipped.\n")
                continue
        else:
            # Read-only tools run automatically, but we still show what's happening
            print(f"\n  → Running {name}({args})")

        # Execute the tool
        try:
            result = FUNCTIONS[name](**args)
        except Exception as e:
            result = f"Error: {e}"

        history.append({"role": "tool", "name": name, "content": str(result)})

    # Second call: model summarizes the tool results into a final answer
    final = ollama.chat(model=MODEL, messages=history)
    final_msg = final["message"]
    history.append(final_msg)
    print(f"\nAssistant: {final_msg['content']}\n")


def main():
    print("=" * 55)
    print("  Local AI Assistant (Phase 1) — type 'quit' to exit")
    print(f"  Model: {MODEL}")
    print("=" * 55)

    history = [{
        "role": "system",
        "content": (
            "You are a helpful local assistant on a Mac. "
            "When the user asks you to do something on their computer, "
            "use the available tools. Be concise."
        ),
    }]

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break

        if user_input.lower() in {"quit", "exit"}:
            print("Goodbye.")
            break
        if not user_input:
            continue

        try:
            handle_turn(user_input, history)
        except Exception as e:
            print(f"\n[error] {e}\n")


if __name__ == "__main__":
    main()

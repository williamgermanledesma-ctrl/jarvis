"""
registry.py
-----------
This is the single place where you tell the model what tools exist.

Each tool has:
  - a SCHEMA (what the model reads to decide when/how to call it)
  - a FUNCTION (the actual Python code that runs)
  - a "destructive" flag (if True, the user must approve before it runs)

To add a new tool:
  1. Write the function in tools/actions.py
  2. Add an entry to TOOLS below
"""

from tools import actions
import memory
import sandbox
import code_index

TOOLS = [
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "get_disk_usage",
                "description": "Get disk usage of the main drive",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        "function": actions.get_disk_usage,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List the files inside a directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "Path to the directory, e.g. ~/Downloads",
                        }
                    },
                    "required": ["directory"],
                },
            },
        },
        "function": actions.list_files,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "read_text_file",
                "description": "Read the contents of a text file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Path to the file, e.g. ~/notes/todo.txt",
                        }
                    },
                    "required": ["filepath"],
                },
            },
        },
        "function": actions.read_text_file,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "run_shell_command",
                "description": (
                    "Run a shell/terminal command on the Mac and return its output. "
                    "Use for tasks not covered by other tools."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to run, e.g. 'ls -la ~/Desktop'",
                        }
                    },
                    "required": ["command"],
                },
            },
        },
        "function": actions.run_shell_command,
        "destructive": True,  # <-- always requires approval
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "run_safe_command",
                "description": (
                    "Run a pre-approved read-only system command. "
                    "Allowed names: battery, uptime, date, wifi, ip, memory."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "One of: battery, uptime, date, wifi, ip, memory",
                        }
                    },
                    "required": ["name"],
                },
            },
        },
        "function": actions.run_safe_command,
        "destructive": False,  # safe: allow-listed, read-only
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "write_text_file",
                "description": "Write text content to a file (creates or overwrites it)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Path to write, e.g. ~/notes/todo.txt",
                        },
                        "content": {
                            "type": "string",
                            "description": "The text to write into the file",
                        },
                    },
                    "required": ["filepath", "content"],
                },
            },
        },
        "function": actions.write_text_file,
        "destructive": True,  # can overwrite files
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "remember",
                "description": (
                    "Save a fact, preference, or note to long-term memory so it "
                    "can be recalled in future conversations. Use when the user "
                    "shares something worth remembering (e.g. 'I prefer X', "
                    "'my project is called Y', 'remind me that Z')."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The fact to remember, as a short sentence",
                        }
                    },
                    "required": ["fact"],
                },
            },
        },
        "function": memory.remember,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "recall",
                "description": (
                    "Search long-term memory for facts relevant to a query. "
                    "Use when the user asks about something they may have told "
                    "you before."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to look up in memory",
                        }
                    },
                    "required": ["query"],
                },
            },
        },
        "function": memory.recall,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "jarvis_run_tests",
                "description": (
                    "Run Python implementation code together with pytest tests in "
                    "an isolated sandbox, and return whether they passed plus any "
                    "error output. Use this in the Write-Test-Fix loop: write code, "
                    "write tests, run them here, and fix based on the result."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "implementation_code": {
                            "type": "string",
                            "description": "The full module under test (saved as solution.py)",
                        },
                        "test_code": {
                            "type": "string",
                            "description": (
                                "The full pytest test file (saved as test_solution.py). "
                                "Import the code under test with: from solution import ..."
                            ),
                        },
                    },
                    "required": ["implementation_code", "test_code"],
                },
            },
        },
        "function": sandbox.jarvis_run_tests,
        "destructive": False,  # safe: isolated temp dir, no network, timeout
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "list_workspace_files",
                "description": "List files in the workspace, including anything the user uploaded",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subdir": {
                            "type": "string",
                            "description": "Optional subfolder within the workspace",
                        }
                    },
                },
            },
        },
        "function": actions.list_workspace_files,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "describe_uploaded_file",
                "description": (
                    "Describe an uploaded file: preview text files, or note that "
                    "an image needs a vision model. Use after the user uploads."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Path to the uploaded file in the workspace",
                        }
                    },
                    "required": ["filename"],
                },
            },
        },
        "function": actions.describe_uploaded_file,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "scan_codebase",
                "description": (
                    "Scan a code project to understand its structure before "
                    "editing: returns a file tree plus previews of each code "
                    "file. Use this first when asked to work on or understand a "
                    "multi-file project."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "Subfolder of the workspace to scan (blank = whole workspace)",
                        }
                    },
                },
            },
        },
        "function": actions.scan_codebase,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "apply_patch",
                "description": (
                    "Surgically edit a file by replacing an exact block of text "
                    "with a new block, instead of rewriting the whole file. "
                    "Prefer this over write_text_file for editing existing code. "
                    "The search_block must match the current file text exactly and "
                    "appear only once."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string", "description": "File to edit"},
                        "search_block": {"type": "string", "description": "Exact text to find (include enough lines to be unique)"},
                        "replace_block": {"type": "string", "description": "Text to replace it with"},
                    },
                    "required": ["filepath", "search_block", "replace_block"],
                },
            },
        },
        "function": actions.apply_patch,
        "destructive": True,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "index_codebase",
                "description": (
                    "Parse the project's Python code into a searchable symbol "
                    "index (functions, classes, docstrings) stored in the vector "
                    "DB. Run this once before semantic code questions on a large "
                    "project."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "Subfolder to index (blank = whole workspace)"},
                    },
                },
            },
        },
        "function": code_index.index_codebase_tool,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": (
                    "Semantically search the project's indexed code symbols to "
                    "find relevant functions/classes — e.g. 'where is auth "
                    "handled?' — without reading every file. Run index_codebase "
                    "first if needed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to look for in the code"},
                    },
                    "required": ["query"],
                },
            },
        },
        "function": code_index.search_code_tool,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "run_project_tests",
                "description": (
                    "Detect and run the project's test suite (pytest) and return "
                    "pass/fail with output. Use to verify code behaves correctly, "
                    "not just that it parses."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "Subfolder to test (blank = workspace)"},
                    },
                },
            },
        },
        "function": actions.run_project_tests,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "Search the web for current info — API docs, library updates, "
                    "or an unfamiliar error message. Only works if the user has "
                    "enabled web search (it sends the query to the internet)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for"},
                    },
                    "required": ["query"],
                },
            },
        },
        "function": actions.web_search,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "run_linter",
                "description": (
                    "Run a language-appropriate linter/compiler check on a file "
                    "(flake8/pyflakes, eslint, rustc, gofmt). Use to catch style "
                    "and scoping issues beyond syntax."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string", "description": "File to lint"},
                    },
                    "required": ["filepath"],
                },
            },
        },
        "function": actions.run_linter,
        "destructive": False,
    },
    {
        "schema": {
            "type": "function",
            "function": {
                "name": "deploy_test",
                "description": (
                    "Spin up the project in an ephemeral Docker container and run "
                    "integration tests against the running app before approval — "
                    "applies init steps (e.g. rename .env.example), installs deps, "
                    "starts the backend, runs tests, tears down. Requires Docker."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "Project subfolder (blank = workspace)"},
                        "setup_cmd": {"type": "string", "description": "Setup shell command (optional; inferred if blank)"},
                        "run_cmd": {"type": "string", "description": "Command to start the app (optional, e.g. 'python server.py')"},
                        "test_cmd": {"type": "string", "description": "Test command (optional; defaults to pytest)"},
                    },
                },
            },
        },
        "function": actions.deploy_test,
        "destructive": False,
    },
]


# Convenience lookups built from TOOLS above
SCHEMAS = [t["schema"] for t in TOOLS]
FUNCTIONS = {t["schema"]["function"]["name"]: t["function"] for t in TOOLS}
DESTRUCTIVE = {t["schema"]["function"]["name"]: t["destructive"] for t in TOOLS}

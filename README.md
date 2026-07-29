# commands-wrapper (CW)

Wraps multi-step shell sequences into a single custom named command. Use it to chain multiple steps together into one.

```bash
    - command: "shell command to run"
    - send: "text to send to the process"
    - press_key: "key to press"
    - wait: "seconds to wait"
```

## Installation

Unix (Linux/macOS/WSL):

```bash
git clone --depth 1 https://github.com/vivid0o0/commands-wrapper.git
bash commands-wrapper/.commands-wrapper/install.sh
```

Windows (PowerShell):

```powershell
git clone --depth 1 https://github.com/vivid0o0/commands-wrapper.git
& .\commands-wrapper\.commands-wrapper\install.ps1
```

## Usage

### easy (for humans)

Run interactive TUI using:
```bash
cw # or commands-wrapper
```

This allows you to view all your "wraps", modify them or add new ones.

### AI agents (skill)

If you're an AI agent, configure it directly like this:
```bash
commands-wrapper add --yaml <<'EOF'
command-name:
  description: "..."
  steps:
    - command: "..."
EOF
```
> See `YAML format` below

## YAML format

```yaml
wrap-name: # The name you'll type to run this wrap
  description: "What this command does"
  steps:
    - command: "shell command to run"
    - send: "text to send to the process"
    - press_key: "key to press"
    - wait: "seconds to wait"
```

## Commands

```bash
commands-wrapper list
```

```bash
commands-wrapper remove "command-name"
```

```bash
cw <command-name>
commands-wrapper <command-name>
```

```bash
commands-wrapper update # or upd
```
> you can also rerun the install command


```bash
commands-wrapper --uninstall
```

## Support

If you found this project useful, please consider starring the repo and dropping me a follow for more stuff like this :)
It takes less than a minute and helps a lot ❤️

> If you find a bug or unexpected behavior, please report it!

---

### More projects

See more projects from [@vivid0o0](https://github.com/vivid0o0).

---

If you want to show extra love, consider *[buying me a coffee](https://buymeacoffee.com/vivid0o0)*! ☕


[![Buy Me a Coffee](https://imgs.search.brave.com/FolmlC7tneei1JY_QhD9teOLwsU3rivglA3z2wWgJL8/rs:fit:860:0:0:0/g:ce/aHR0cHM6Ly93aG9w/LmNvbS9ibG9nL2Nv/bnRlbnQvaW1hZ2Vz/L3NpemUvdzIwMDAv/MjAyNC8wNi9XaGF0/LWlzLUJ1eS1NZS1h/LUNvZmZlZS53ZWJw)](https://buymeacoffee.com/vivid0o0)

## License

[MIT](LICENSE)
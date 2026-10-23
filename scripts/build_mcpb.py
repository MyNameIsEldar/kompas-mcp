"""Сборка расширения Claude Desktop (.mcpb) из исходников плагина.

Запуск:  python scripts/build_mcpb.py
Результат: dist/kompas.mcpb — установить двойным щелчком или через Claude Desktop → Settings → Extensions.

Зависимости в пакет не кладутся (pywin32 не работает из подкаталога): расширение запускает системный
python, в котором должны быть установлены пакеты из requirements.txt.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "dist", "mcpb")
OUT = os.path.join(ROOT, "dist", "kompas.mcpb")
SERVER_FILES = ["server.py", "kompas_com.py", "drawing2d.py", "model3d.py", "winutil.py",
                "requirements.txt", "README.md"]

sys.path.insert(0, ROOT)


async def tool_list() -> list[dict]:
    import server
    tools = await server.mcp.list_tools()
    return [{"name": t.name, "description": (t.description or "").strip().splitlines()[0]} for t in tools]


def main() -> None:
    plugin = json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8"))
    manifest = {
        "manifest_version": "0.3",
        "name": "kompas",
        "display_name": "КОМПАС-3D",
        "version": plugin["version"],
        "description": plugin["description"],
        "long_description": (
            "Claude работает с документами КОМПАС-3D через COM API: создаёт и правит чертежи, фрагменты и "
            "детали, ставит размеры, заполняет основную надпись, выполняет 3D-операции и сам смотрит на "
            "результат картинкой. Нужен установленный КОМПАС-3D и Python с пакетами: "
            "pip install mcp pywin32 pillow pydantic comtypes"),
        "author": plugin.get("author", {"name": "kompas-mcp"}),
        "repository": {"type": "git", "url": "https://github.com/MyNameIsEldar/kompas-mcp"},
        "keywords": plugin.get("keywords", []),
        "server": {
            "type": "python",
            "entry_point": "server.py",
            "mcp_config": {
                "command": "python",
                "args": ["${__dirname}/server.py"],
                "env": {"PYTHONUTF8": "1"},
            },
        },
        "tools": asyncio.run(tool_list()),
        "compatibility": {"platforms": ["win32"], "runtimes": {"python": ">=3.10"}},
    }

    shutil.rmtree(BUILD, ignore_errors=True)
    os.makedirs(BUILD)
    for f in SERVER_FILES:
        shutil.copy2(os.path.join(ROOT, f), BUILD)
    with open(os.path.join(BUILD, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    npx = shutil.which("npx") or "npx"
    subprocess.run([npx, "-y", "@anthropic-ai/mcpb", "validate", os.path.join(BUILD, "manifest.json")], check=True)
    if os.path.exists(OUT):
        os.remove(OUT)
    subprocess.run([npx, "-y", "@anthropic-ai/mcpb", "pack", BUILD, OUT], check=True)
    print(f"Готово: {OUT} ({len(manifest['tools'])} инструментов)")


if __name__ == "__main__":
    main()

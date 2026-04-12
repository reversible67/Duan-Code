import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools import read_file, write_to_file, run_terminal_command, list_directory, search_in_files, web_search

TEST_DIR = os.path.dirname(__file__)
TEMP_FILE = os.path.join(TEST_DIR, "temp_test_file.txt")


def test_write_to_file():
    result = write_to_file(TEMP_FILE, "Hello\nWorld")
    assert result == "写入成功", f"写入失败: {result}"
    print("✅ write_to_file 通过")


def test_read_file():
    content = read_file(TEMP_FILE)
    assert "Hello" in content, f"读取内容异常: {content}"
    print(f"✅ read_file 通过，内容: {repr(content)}")


def test_run_terminal_command():
    result = run_terminal_command("echo hello_from_test")
    assert "hello_from_test" in result or result == "执行成功", f"命令执行异常: {result}"
    print(f"✅ run_terminal_command 通过，输出: {repr(result)}")


def test_list_directory():
    result = list_directory(TEST_DIR)
    assert "test_tools.py" in result, f"目录列表中未找到当前文件: {result}"
    print(f"✅ list_directory 通过，目录结构:\n{result}")


def test_search_in_files():
    result = search_in_files("Hello", TEST_DIR)
    assert TEMP_FILE in result or "Hello" in result, f"搜索未命中: {result}"
    print(f"✅ search_in_files 通过，搜索结果:\n{result}")


def test_web_search():
    result = web_search("Python programming", max_results=2)
    assert "搜索失败" not in result, f"搜索失败: {result}"
    assert "结果" in result, f"搜索结果格式异常: {result}"
    print(f"✅ web_search 通过，搜索结果:\n{result}")


def cleanup():
    if os.path.exists(TEMP_FILE):
        os.remove(TEMP_FILE)
    print("\n🧹 临时文件已清理")


if __name__ == "__main__":
    print("=" * 50)
    print("开始测试所有 tools")
    print("=" * 50)

    test_write_to_file()
    test_read_file()
    test_run_terminal_command()
    test_list_directory()
    test_search_in_files()
    test_web_search()
    cleanup()

    print("\n" + "=" * 50)
    print("✅ 所有测试通过！")
    print("=" * 50)

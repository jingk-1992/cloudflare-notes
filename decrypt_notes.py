#!/usr/bin/env python3
import argparse
import base64
import getpass
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from Crypto.Cipher import AES


OPENSSL_SALT_PREFIX = b"Salted__"
SCRIPT_DIR = Path(__file__).resolve().parent
FIXED_EXPORT_DIR = "exported_notes"


def evp_bytes_to_key(password: bytes, salt: bytes, key_len: int = 32, iv_len: int = 16) -> bytes:
    """CryptoJS passphrase AES uses OpenSSL-compatible MD5 key derivation."""
    derived = b""
    block = b""
    while len(derived) < key_len + iv_len:
        block = hashlib.md5(block + password + salt).digest()
        derived += block
    return derived


def decrypt_cryptojs_aes(ciphertext: str, passphrase: str) -> str:
    raw = base64.b64decode(ciphertext)
    if not raw.startswith(OPENSSL_SALT_PREFIX):
        raise ValueError("密文不是 CryptoJS/OpenSSL Salted 格式")

    salt = raw[8:16]
    encrypted = raw[16:]
    key_iv = evp_bytes_to_key(passphrase.encode("utf-8"), salt)
    key = key_iv[:32]
    iv = key_iv[32:48]

    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = cipher.decrypt(encrypted)
    pad_len = padded[-1]
    if pad_len < 1 or pad_len > AES.block_size:
        raise ValueError("解密失败：padding 不正确，可能是 key 错误")

    plain = padded[:-pad_len]
    return plain.decode("utf-8")


def load_records(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("用户数据文件必须是 JSON 数组")
    return data


def decrypt_record(record: Dict[str, Any], passphrase: str) -> Dict[str, Any]:
    encrypted_data = record.get("encrypted_data")
    if not isinstance(encrypted_data, str):
        raise ValueError(f"笔记 {record.get('id', '<unknown>')} 缺少 encrypted_data")

    plain_text = decrypt_cryptojs_aes(encrypted_data, passphrase)
    note = json.loads(plain_text)
    if not isinstance(note, dict):
        raise ValueError(f"笔记 {record.get('id', '<unknown>')} 解密后不是对象")
    return note


def note_to_markdown(note: Dict[str, Any]) -> str:
    title = str(note.get("title") or "未命名笔记")
    content = str(note.get("content") or "")
    tags = note.get("tags") if isinstance(note.get("tags"), list) else []
    summary = str(note.get("summary") or "")

    lines = [f"# {title}", ""]
    if summary:
        lines += [f"> {summary}", ""]
    if tags:
        lines += ["标签：" + ", ".join(str(tag) for tag in tags), ""]
    lines.append(content)
    if not content.endswith("\n"):
        lines.append("")
    return "\n".join(lines)


def safe_filename(value: str) -> str:
    keep = []
    for char in value.strip():
        if char.isalnum() or char in ("-", "_", " ", "."):
            keep.append(char)
        else:
            keep.append("_")
    name = "".join(keep).strip() or "note"
    return name[:80]


def resolve_near_script(path: Path) -> Path:
    if path.is_absolute():
        return path
    return SCRIPT_DIR / path


def reset_directory(path: Path) -> None:
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    path.mkdir(parents=True, exist_ok=True)


def write_output(notes: List[Dict[str, Any]], output_format: str, out: Optional[Path]) -> Path:
    if output_format == "json":
        text = json.dumps(notes, ensure_ascii=False, indent=2)
        target = resolve_near_script(out) if out else SCRIPT_DIR / "decrypted_notes.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    if output_format == "markdown":
        target_dir = SCRIPT_DIR / FIXED_EXPORT_DIR
        reset_directory(target_dir)
        for index, note in enumerate(notes, start=1):
            title = safe_filename(str(note.get("title") or f"note_{index}"))
            note_id = safe_filename(str(note.get("id") or index))
            target = target_dir / f"{index:03d}_{title}_{note_id}.md"
            target.write_text(note_to_markdown(note), encoding="utf-8")
        return target_dir

    raise ValueError(f"不支持的输出格式：{output_format}")


def main(default_input: Optional[str] = None, default_key: Optional[str] = None) -> None:
    parser = argparse.ArgumentParser(description="解密 Cloudflare R2 中导出的加密笔记 JSON")
    parser.add_argument("-i", "--input", type=Path, help="和 decrypt_notes.py 同目录的用户 JSON 文件名")
    parser.add_argument("-k", "--key", help="加密 key；不传则交互式输入")
    parser.add_argument("--note-id", help="只解密指定笔记 ID")
    parser.add_argument(
        "-f",
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        help="输出格式，默认 markdown",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        help="兼容旧命令参数；markdown 固定输出到 exported_notes 并覆盖",
    )
    args = parser.parse_args()

    input_path = args.input or (Path(default_input) if default_input else None)
    if not input_path:
        raise SystemExit("请使用 -i 指定 JSON 文件，例如：python decrypt_notes.py -i user_data_213.json -k xx -f markdown -o exported_notes")

    passphrase = args.key or default_key or getpass.getpass("请输入加密 key: ")
    records = load_records(resolve_near_script(input_path))
    if args.note_id:
        records = [record for record in records if record.get("id") == args.note_id]
        if not records:
            raise SystemExit(f"未找到笔记 ID：{args.note_id}")

    notes = []
    for record in records:
        try:
            notes.append(decrypt_record(record, passphrase))
        except Exception as exc:
            note_id = record.get("id", "<unknown>")
            raise SystemExit(f"解密笔记 {note_id} 失败：{exc}") from exc

    output_path = write_output(notes, args.format, args.out)
    print(f"解密完成，输出位置：{output_path}")


if __name__ == "__main__":
    file_path = "user_data_user1.json"
    key = "pwd1"
    main(file_path, key)

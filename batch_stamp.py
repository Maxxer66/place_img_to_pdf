"""
Пакетное наложение подписей и печатей на PDF.
- Исходные PDF берутся из папки source/ рядом со скриптом.
- Результаты сохраняются в папку done/ с суффиксом "-mod".
- Настройки берутся из config.json (формат задаёт setup_by_text.py).
- Правило с непустым 'anchor_text' применяется ко ВСЕМ страницам,
  где встречается якорь. Правило только с 'x'/'y' — к указанной 'page'.
"""

import json
import shutil
import sys
from pathlib import Path

from PIL import Image

try:
    import pymupdf as fitz  # новый API
except ImportError:
    import fitz  # старый API

import pdf_sign_kit as psk


# ---------------------------------------------------------------- config
def load_config(script_dir: Path) -> dict:
    config_path = script_dir / "config.json"
    if not config_path.exists():
        print("❌ Файл config.json не найден рядом со скриптом.")
        sys.exit(1)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка в config.json: {e}")
        sys.exit(1)


# ---------------------------------------------------------------- images
def prepare_images(rules: list[dict], threshold: int, script_dir: Path) -> dict[str, str]:
    """Удаляет фон. Масштабирование НЕ делаем — его выполнит insert_image
    через fitz.Rect (качество сохраняется).
    Возвращает {имя: путь_к_temp}.
    """
    names: list[str] = []
    seen: set[str] = set()
    for rule in rules:
        name = rule["image"]
        if name not in seen:
            seen.add(name)
            names.append(name)

    mapping: dict[str, str] = {}
    for name in names:
        src = script_dir / name
        if not src.exists():
            print(f"⚠️  Изображение не найдено: {name}")
            continue
        cleaned = psk.remove_background(str(src), threshold=threshold)
        temp_path = script_dir / f"temp_{Path(name).stem}.png"
        cleaned.save(str(temp_path))
        mapping[name] = str(temp_path)
    return mapping


# ---------------------------------------------------------------- rules
def apply_rule(pdf_path: Path, rule: dict, image_map: dict[str, str]) -> int:
    """Применяет одно правило к PDF. Возвращает число страниц, куда что-то наложено."""
    name = rule["image"]
    if name not in image_map:
        print(f"  ⚠️  Пропуск: изображение {name} не подготовлено.")
        return 0

    width_pt = float(rule.get("width") or 80)
    if width_pt <= 0:
        width_pt = 80.0

    img_path = image_map[name]
    try:
        with Image.open(img_path) as im:
            orig_w, orig_h = im.size
    except Exception as e:
        print(f"  ❌ Не удалось открыть {img_path}: {e}")
        return 0
    if orig_w <= 0:
        return 0
    height_pt = orig_h * (width_pt / orig_w)

    anchor_text = (rule.get("anchor_text") or "").strip()
    has_anchor = bool(anchor_text)

    # offset_x/offset_y — смещение от word.x0/word.y0 (top-based, как в setup)
    occ = int(rule.get("anchor_occurrence", 0))
    off_x = float(rule.get("offset_x", 0.0))
    off_y = float(rule.get("offset_y", 0.0))

    applied_pages: list[int] = []

    doc = fitz.open(str(pdf_path))
    try:
        if has_anchor:
            # Идём по всем страницам: шаблонное поведение
            for pno in range(doc.page_count):
                page = doc[pno]
                try:
                    words = page.get_text("words")
                except Exception:
                    continue
                matching = [w for w in words if w[4] == anchor_text]
                if len(matching) <= occ:
                    continue
                matching.sort(key=lambda w: (round(w[1], 1), round(w[0], 1)))
                anchor = matching[occ]
                x = anchor[0] + off_x
                y = anchor[1] + off_y
                rect = fitz.Rect(x, y, x + width_pt, y + height_pt)
                page.insert_image(rect, filename=img_path)
                applied_pages.append(pno)
        elif "x" in rule and "y" in rule:
            # Резервный путь: явные координаты + явная страница
            pno = int(rule.get("page", 0))
            if 0 <= pno < doc.page_count:
                page = doc[pno]
                x = float(rule["x"])
                y = float(rule["y"])
                rect = fitz.Rect(x, y, x + width_pt, y + height_pt)
                page.insert_image(rect, filename=img_path)
                applied_pages.append(pno)
        else:
            print(f"  ⚠️  В правиле для {name} нет ни 'anchor_text', ни 'x'/'y'.")
            return 0

        if not applied_pages:
            print(f"  ⚠️  {name}: якорь '{anchor_text or '—'}' не найден ни на одной странице.")
            return 0

        # Сохраняем через временный файл — надёжнее, чем saveIncr
        temp_out = str(pdf_path) + ".tmp.pdf"
        doc.save(temp_out, garbage=4, deflate=True)
        doc.close()
        Path(temp_out).replace(pdf_path)
        pages_str = ", ".join(str(p + 1) for p in applied_pages)
        print(f"  ✅ {name} → стр. {pages_str}")
        return len(applied_pages)
    except Exception as e:
        try:
            doc.close()
        except Exception:
            pass
        print(f"  ❌ Ошибка наложения {name}: {e}")
        return 0


# ---------------------------------------------------------------- main
def main():
    script_dir = Path(__file__).resolve().parent
    source_dir = script_dir / "source"
    output_dir = script_dir / "done"

    if not source_dir.exists():
        print(f"❌ Папка source/ не найдена: {source_dir}")
        sys.exit(1)
    output_dir.mkdir(exist_ok=True)

    config = load_config(script_dir)
    threshold = int(config.get("background_threshold", 235))
    rules = config.get("rules", [])
    if not rules:
        print("❌ В config.json нет ни одного правила в 'rules'.")
        sys.exit(1)

    pdf_files = sorted(source_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"В папке '{source_dir}' нет PDF-файлов.")
        sys.exit(1)

    print("Подготовка изображений (удаление фона)...")
    image_map = prepare_images(rules, threshold, script_dir)
    print(f"Обработано изображений: {len(image_map)}\n")

    for pdf_path in pdf_files:
        output_path = output_dir / f"{pdf_path.stem}-mod{pdf_path.suffix}"
        print(f"Обработка: {pdf_path.name}  →  {output_path.name}")
        shutil.copy2(pdf_path, output_path)

        total = 0
        for r in rules:
            total += apply_rule(output_path, r, image_map)
        print(f"  Итого наложений: {total}\n")

    for temp in image_map.values():
        Path(temp).unlink(missing_ok=True)

    print(f"Готово. Результаты в папке: {output_dir}")


if __name__ == "__main__":
    main()
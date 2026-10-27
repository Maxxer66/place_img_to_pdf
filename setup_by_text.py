"""
Визуальная настройка привязки подписей и печатей к текстовым полям PDF.

Пользователь кликает по слову в PDF — это слово становится якорем для
выбранного элемента. Элемент можно перетащить мышью по canvas и задать
нужную ширину. Результат записывается в config.json в виде:

{
  "background_threshold": 235,
  "rules": [
    {
      "image": "signature_1.png",     # файл картинки
      "width": 80,                    # ширина в pt (масштаб)
      "x": 350.5, "y": 120.3,         # абсолютные PDF-координаты (fallback)
      "anchor_text": "План",          # найденное слово-якорь
      "anchor_occurrence": 0,         # какое вхождение слова (0-based)
      "offset_x": 15.0,               # offset от x0 якоря (pt)
      "offset_y": 5.0,                # offset от y0 якоря (pt)
      "page": 0                       # страница (по умолчанию 0)
    }
  ]
}

batch_stamp.py находит `anchor_text` (с учётом `anchor_occurrence`) и
рисует картинку в точке `(word.x0 + offset_x, word.y0 + offset_y)`
размером `width` pt.
"""

import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import fitz  # PyMuPDF
from PIL import Image, ImageTk

# ================= НАСТРОЙКИ =================
SEARCH_AREA = (0.6, 0.6)  # не используется (все слова кликабельны)

DEFAULT_SIGNATURES = 2
DEFAULT_SEALS = 3
MAX_ELEMENTS = 20
# =============================================


# ---------------------------------------------------------------- helpers
def get_existing_counts(config_path: Path) -> tuple[int | None, int | None]:
    if not config_path.exists():
        return None, None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        rules = config.get("rules", [])
        sig = sum(1 for r in rules if r.get("image", "").startswith("signature_"))
        seal = sum(1 for r in rules if r.get("image", "").startswith("seal_"))
        return sig, seal
    except Exception:
        return None, None


def build_elements(sig_count: int, seal_count: int) -> list[str]:
    elements = []
    for i in range(1, sig_count + 1):
        elements.append(f"signature_{i}.png")
    for i in range(1, seal_count + 1):
        elements.append(f"seal_{i}.png")
    return elements


# ---------------------------------------------------------------- dialog
class TextAnchorDialog(tk.Tk):
    def __init__(self, pdf_path: Path, elements: list[str]):
        super().__init__()
        self.title(f"Настройка привязок — {pdf_path.name}")
        self.pdf_path = pdf_path
        self.elements = elements
        self.current_index = 0
        self.rules: list[dict] = []

        # --- состояние превью ---
        self._selected_word: dict | None = None
        self._current_image_path: str | None = None
        self._overlay_id: int | None = None
        self._placed_img_refs: dict[str, ImageTk.PhotoImage] = {}
        self._is_placing: bool = False

        # --- Загрузка PDF ---
        doc = fitz.open(pdf_path)
        page = doc[0]

        self.page_rect = page.rect
        self.page_width = self.page_rect.width
        self.page_height = self.page_rect.height

        self.all_words = [
            {"x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3], "text": w[4]}
            for w in page.get_text("words")
        ]
        self.visible_words = list(self.all_words)

        zoom = 2.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        doc.close()

        self.photo = ImageTk.PhotoImage(img)
        self.zoom = zoom
        # Реальный коэффициент "pt PDF → px на canvas"
        self.px_per_pt = pix.width / self.page_width

        # --- Загрузка картинок элементов ---
        self._element_images: dict[str, Image.Image] = {}
        self._script_dir = Path(__file__).resolve().parent
        for el in self.elements:
            el_path = self._script_dir / el
            if el_path.exists():
                self._element_images[el] = Image.open(el_path)

        # --- Верхняя панель ---
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ctrl_frame = ttk.Frame(top)
        ctrl_frame.pack(side="right", fill="y", padx=(8, 0))

        ttk.Label(ctrl_frame, text="Ширина:", font=("Arial", 9)).pack(pady=(0, 2))
        self.width_var = tk.IntVar(value=80)
        self.width_spin = ttk.Spinbox(
            ctrl_frame, from_=10, to=500, textvariable=self.width_var, width=8,
        )
        self.width_spin.pack()
        self.width_spin.bind(
            "<KeyRelease>",
            lambda e: (self.width_slider.set(int(self.width_var.get())),
                       self._on_width_change(None)),
        )

        self.width_slider = ttk.Scale(
            ctrl_frame, from_=10, to=500, command=self._on_width_change,
        )
        self.width_slider.pack(fill="x", pady=4)
        self.width_slider.set(80)

        self.width_label = ttk.Label(
            ctrl_frame, text="80 pt", font=("Arial", 8), foreground="#555",
        )
        self.width_label.pack()

        self.label = ttk.Label(
            top,
            text="ЛКМ = по слову | ПКМ = произвольная точка | тяните картинку мышью",
            font=("Arial", 9), foreground="#555",
        )
        self.label.pack(side="left")

        self.combo = ttk.Combobox(top, values=self.elements, state="readonly", width=20)
        self.combo.current(0)
        self.combo.pack(side="right")
        self.combo.bind("<<ComboboxSelected>>", self._on_element_change)

        self.save_btn = ttk.Button(
            top, text="💾 Сохранить и выйти", command=self._save_and_close,
        )
        self.save_btn.pack(side="right", padx=8)

        # --- Scrollable canvas ---
        canvas_frame = ttk.Frame(self)
        canvas_frame.pack(padx=8, pady=8, fill="both", expand=True)

        self.vscroll = ttk.Scrollbar(canvas_frame, orient="vertical")
        self.hscroll = ttk.Scrollbar(canvas_frame, orient="horizontal")
        self.canvas = tk.Canvas(
            canvas_frame,
            width=max(600, self.photo.width()),
            height=max(400, self.photo.height()),
            cursor="hand2",
            xscrollcommand=self.hscroll.set,
            yscrollcommand=self.vscroll.set,
        )
        self.vscroll.pack(side="right", fill="y")
        self.hscroll.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vscroll.config(command=self.canvas.yview)
        self.hscroll.config(command=self.canvas.xview)

        self.img_width = self.photo.width()
        self.img_height = self.photo.height()

        self.canvas.bind("<Configure>", lambda e: self._redraw_overlay())

        # Сначала прямоугольники слов
        for word in self.all_words:
            cx0 = word["x0"] * self.px_per_pt
            cy0 = word["y0"] * self.px_per_pt
            cx1 = word["x1"] * self.px_per_pt
            cy1 = word["y1"] * self.px_per_pt
            rect = self.canvas.create_rectangle(
                cx0, cy0, cx1, cy1,
                outline="#0000FF", width=1, fill="",
                tags="word_rect",
            )
            self.canvas.tag_bind(
                rect, "<Button-1>",
                lambda e, w=word, r=rect: self._on_word_click(w, r),
            )

        # Затем изображение (ниже по z-order)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo, tags="pdf_img")
        self.canvas.tag_lower("pdf_img")

        self.canvas.config(scrollregion=self.canvas.bbox("all"))

        self.canvas.bind("<Button-3>", self._on_canvas_right_click)

        # --- Нижняя панель статуса ---
        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")

        self.status = ttk.Label(
            bottom, text="Осталось разместить: " + ", ".join(self.elements),
        )
        self.status.pack(side="left")

        ttk.Button(bottom, text="Пропустить элемент", command=self._skip).pack(
            side="right", padx=4,
        )

    # ---------------------------------------------------------------- UI
    def _on_element_change(self, _event=None):
        self.current_index = self.combo.current()
        self._selected_word = None
        self._current_image_path = None
        if self._overlay_id is not None:
            self.canvas.delete(self._overlay_id)
            self._overlay_id = None
        self.label.config(
            text=f"Элемент: {self.elements[self.current_index]} — кликните по тексту",
        )
        element_name = self.elements[self.current_index]
        for rule in self.rules:
            if rule.get("image") == element_name and "width" in rule:
                self.width_var.set(rule["width"])
                self.width_slider.set(rule["width"])
                self.width_label.config(text=f"{rule['width']} pt")
                break
        else:
            self.width_var.set(80)
            self.width_slider.set(80)
            self.width_label.config(text="80 pt")

    def _advance(self):
        self._draw_placed_elements()
        self.current_index += 1
        if self._overlay_id is not None:
            self.canvas.delete(self._overlay_id)
            self._overlay_id = None
        if self.current_index < len(self.elements):
            self.combo.current(self.current_index)
            self.label.config(
                text=f"Элемент: {self.elements[self.current_index]} — кликните по тексту (или ПКМ)",
            )
        else:
            self.label.config(text="✅ Все элементы размещены! Нажмите «Сохранить и выйти».")
            self.after(300, self._show_save_reminder)
        remaining = self.elements[self.current_index:]
        self.status.config(
            text="Осталось разместить: " + (", ".join(remaining) if remaining else "—"),
        )

    def _show_save_reminder(self):
        dlg = tk.Toplevel(self)
        dlg.title("Готово")
        dlg.geometry("400x180")
        dlg.transient(self)
        dlg.grab_set()
        dlg.attributes("-topmost", True)
        ttk.Label(
            dlg,
            text=f"Все {len(self.rules)} элементов размещены!\n\n"
                 "Нажмите «Сохранить и выйти».",
            font=("Arial", 10), padding=20,
        ).pack()
        ttk.Button(dlg, text="Понятно", command=dlg.destroy).pack(pady=10)

    def _skip(self):
        if self.current_index < len(self.elements):
            self._advance()

    # --------------------------------------------------------------- width
    def _draw_placed_elements(self):
        """Перерисовывает все уже размещённые элементы с реальным масштабом."""
        for tag in self.canvas.find_withtag("placed_elem"):
            self.canvas.delete(tag)
        self._placed_img_refs = {}

        for rule in self.rules:
            if "x" not in rule or "y" not in rule:
                continue
            image_name = rule["image"]
            x_pt, y_pt = rule["x"], rule["y"]
            width_pt = rule.get("width", 80)

            pil_img = self._element_images.get(image_name)
            if not pil_img:
                continue

            orig_w, orig_h = pil_img.size
            ratio = width_pt / orig_w
            height_pt = max(1, int(orig_h * ratio))

            # FIX: pt → px (иначе картинка вдвое меньше)
            disp_w = max(1, int(width_pt * self.px_per_pt))
            disp_h = max(1, int(height_pt * self.px_per_pt))
            resized = pil_img.resize((disp_w, disp_h), Image.LANCZOS)

            alpha_layer = Image.new("RGBA", resized.size, (0, 0, 0, 0))
            rgba = resized.convert("RGBA")
            alpha_layer.paste(rgba, mask=rgba.split()[3])
            alpha_layer.putalpha(180)

            preview_tk = ImageTk.PhotoImage(alpha_layer)
            self.canvas.create_image(
                x_pt * self.px_per_pt, y_pt * self.px_per_pt,
                anchor="nw", image=preview_tk, tags="placed_elem",
            )
            self._placed_img_refs[image_name] = preview_tk

    def _on_width_change(self, value: str | None = None):
        if value is not None:
            self.width_var.set(int(float(value)))
        w = self.width_var.get()
        self.width_label.config(text=f"{w} pt")

        if self.current_index < len(self.elements):
            current_name = self.elements[self.current_index]
            for rule in self.rules:
                if rule.get("image") == current_name and "x" in rule:
                    rule["width"] = w
                    break

        if self._is_placing and hasattr(self, "_placement_data"):
            self._update_placement_size()
        else:
            self._redraw_overlay()
            if self.rules:
                self._draw_placed_elements()

    def _update_placement_size(self):
        if not hasattr(self, "_placement_data"):
            return
        data = self._placement_data
        pil_img = data["pil_img"]
        width_pt = self.width_var.get()
        orig_w, orig_h = pil_img.size
        ratio = width_pt / orig_w
        height_pt = max(1, int(orig_h * ratio))

        # FIX: pt → px
        disp_w = max(1, int(width_pt * self.px_per_pt))
        disp_h = max(1, int(height_pt * self.px_per_pt))
        resized = pil_img.resize((disp_w, disp_h), Image.LANCZOS)

        alpha_layer = Image.new("RGBA", resized.size, (0, 0, 0, 0))
        rgba = resized.convert("RGBA")
        alpha_layer.paste(rgba, mask=rgba.split()[3])
        alpha_layer.putalpha(128)

        preview_tk = ImageTk.PhotoImage(alpha_layer)
        self.canvas.itemconfig(self._placement_id, image=preview_tk)
        self._placement_img_ref = preview_tk

        # Обновляем hit-область
        if hasattr(self, "_placement_hit_id"):
            bbox = self.canvas.bbox(self._placement_id)
            if bbox:
                self.canvas.coords(
                    self._placement_hit_id,
                    bbox[0], bbox[1], bbox[0] + disp_w, bbox[1] + disp_h,
                )

        data["width_pt"] = width_pt
        data["height_pt"] = height_pt

    # ----------------------------------------------------------- preview
    def _redraw_overlay(self):
        if self._is_placing:
            return
        if self._overlay_id is not None:
            self.canvas.delete(self._overlay_id)
            self._overlay_id = None
        if self._selected_word is None or self._current_image_path is None:
            return

        pil_img = self._element_images.get(self._current_image_path)
        if pil_img is None:
            return

        width_pt = self.width_var.get()
        orig_w, orig_h = pil_img.size
        ratio = width_pt / orig_w
        height_pt = max(1, int(orig_h * ratio))

        disp_w = max(1, int(width_pt * self.px_per_pt))
        disp_h = max(1, int(height_pt * self.px_per_pt))
        resized = pil_img.resize((disp_w, disp_h), Image.LANCZOS)

        w = self._selected_word
        cx0 = w["x1"] * self.px_per_pt
        cy0 = w["y0"] * self.px_per_pt

        alpha_layer = Image.new("RGBA", resized.size, (0, 0, 0, 0))
        rgba = resized.convert("RGBA")
        alpha_layer.paste(rgba, mask=rgba.split()[3])
        alpha_layer.putalpha(100)

        preview_tk = ImageTk.PhotoImage(alpha_layer)
        self._overlay_id = self.canvas.create_image(
            cx0, cy0, anchor="nw", image=preview_tk,
        )
        self._overlay_img_ref = preview_tk

    # ---------------------------------------------------------------- click
    def _on_word_click(self, word: dict, rect_id: int):
        if self.current_index >= len(self.elements):
            messagebox.showinfo("Готово", "Все элементы уже размещены.")
            return

        # Снимаем предыдущую подсветку
        if hasattr(self, "_highlighted_rect_id") and self._highlighted_rect_id:
            try:
                self.canvas.itemconfig(
                    self._highlighted_rect_id, outline="#0000FF", width=1,
                )
            except tk.TclError:
                pass
        self._highlighted_rect_id = rect_id
        self.canvas.itemconfig(rect_id, outline="red", width=3)

        self._selected_word = word
        self._current_image_path = self.elements[self.current_index]
        occurrence = self._compute_occurrence(word)
        self._ask_position(self.elements[self.current_index], word, occurrence)

    def _on_canvas_right_click(self, event):
        if self.current_index >= len(self.elements):
            return
        scroll_x, scroll_y = self.canvas.canvasx(0), self.canvas.canvasy(0)
        pdf_x = (event.x + scroll_x) / self.px_per_pt
        pdf_y = (event.y + scroll_y) / self.px_per_pt

        # FIX: ищем ближайшее реальное слово — оно станет якорем
        nearest = self._find_nearest_word(pdf_x, pdf_y)
        manual_word = {
            "x0": pdf_x, "y0": pdf_y, "x1": pdf_x, "y1": pdf_y,
            "text": nearest["text"] if nearest else "",
        }
        self._selected_word = manual_word
        self._current_image_path = self.elements[self.current_index]
        self._ask_position_manual(self.elements[self.current_index], manual_word)

    def _find_nearest_word(self, x: float, y: float) -> dict | None:
        best, best_d = None, float("inf")
        for w in self.all_words:
            cx = (w["x0"] + w["x1"]) / 2
            cy = (w["y0"] + w["y1"]) / 2
            d = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
            if d < best_d:
                best_d, best = d, w
        return best

    def _compute_occurrence(self, clicked: dict) -> int:
        same = [w for w in self.all_words if w["text"] == clicked["text"]]
        same.sort(key=lambda w: (round(w["y0"], 1), round(w["x0"], 1)))
        for i, w in enumerate(same):
            if abs(w["x0"] - clicked["x0"]) < 1 and abs(w["y0"] - clicked["y0"]) < 1:
                return i
        return 0

    # ---------------------------------------------------------------- place
    def _ask_position(self, element: str, word: dict, occurrence: int):
        self._anchor_word = word
        self._anchor_occurrence = occurrence
        self._element_to_place = element
        self._show_placement_preview(element, word)
        self._add_placement_controls(element)

    def _show_placement_preview(self, element: str, word: dict):
        pil_img = self._element_images.get(element)
        if not pil_img:
            return

        width_pt = self.width_var.get()
        orig_w, orig_h = pil_img.size
        ratio = width_pt / orig_w
        height_pt = max(1, int(orig_h * ratio))

        # FIX: pt → px для отображения
        disp_w = max(1, int(width_pt * self.px_per_pt))
        disp_h = max(1, int(height_pt * self.px_per_pt))
        resized = pil_img.resize((disp_w, disp_h), Image.LANCZOS)

        alpha_layer = Image.new("RGBA", resized.size, (0, 0, 0, 0))
        rgba = resized.convert("RGBA")
        alpha_layer.paste(rgba, mask=rgba.split()[3])
        alpha_layer.putalpha(128)

        preview_tk = ImageTk.PhotoImage(alpha_layer)

        cx0 = (word["x1"] + 5) * self.px_per_pt
        cy0 = word["y0"] * self.px_per_pt

        self._placement_id = self.canvas.create_image(
            cx0, cy0, anchor="nw", image=preview_tk, tags="placement_elem",
        )
        self._placement_img_ref = preview_tk

        # Прозрачный прямоугольник-хитбокс поверх картинки (для drag)
        self._placement_hit_id = self.canvas.create_rectangle(
            cx0, cy0, cx0 + disp_w, cy0 + disp_h,
            fill="", outline="", tags="placement_hit",
        )
        self.canvas.tag_raise("placement_hit")
        self.canvas.tag_bind("placement_hit", "<ButtonPress-1>", self._on_placement_drag_start)
        self.canvas.tag_bind("placement_hit", "<B1-Motion>", self._on_placement_drag_move)

        self._is_placing = True
        self._placement_data = {
            "image": element,
            "word": word,
            "occurrence": getattr(self, "_anchor_occurrence", 0),
            "width_pt": width_pt,
            "height_pt": height_pt,
            "pil_img": pil_img,
            "orig_w": orig_w,
            "orig_h": orig_h,
        }

    def _on_placement_drag_start(self, event):
        self._drag_start = {"x": event.x, "y": event.y}

    def _on_placement_drag_move(self, event):
        if not hasattr(self, "_drag_start"):
            return
        dx = event.x - self._drag_start["x"]
        dy = event.y - self._drag_start["y"]
        self.canvas.move(self._placement_id, dx, dy)
        self.canvas.move("placement_hit", dx, dy)
        self._drag_start["x"] = event.x
        self._drag_start["y"] = event.y

    def _add_placement_controls(self, element: str):
        if hasattr(self, "_control_frame") and self._control_frame is not None:
            self._control_frame.destroy()

        ctrl_frame = tk.Frame(self, relief="raised", borderwidth=2, bg="#FFD700")
        ctrl_frame.place(relx=0, rely=1, anchor="sw", relwidth=1, height=50)
        self._control_frame = ctrl_frame

        ttk.Label(
            ctrl_frame,
            text=f"📍 {element} — перетащите мышью, настройте ширину, затем «Принять»",
            font=("Arial", 10, "bold"), foreground="#000",
        ).pack(side="left", padx=10, pady=8)

        btn_frame = tk.Frame(ctrl_frame, bg="#FFD700")
        btn_frame.pack(side="right", padx=10, pady=5)

        # FIX: убираем несуществующую _gap_var
        def on_confirm():
            self._confirm_placement()

        ttk.Button(btn_frame, text="✅ ПРИНЯТЬ", command=on_confirm).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="❌ ОТМЕНА", command=self._cancel_placement).pack(side="right", padx=5)

    def _confirm_placement(self):
        if not hasattr(self, "_placement_data"):
            return
        data = self._placement_data
        element = data["image"]
        word = data["word"]
        occurrence = data.get("occurrence", 0)

        bbox = self.canvas.bbox(self._placement_id)
        if bbox:
            pdf_x = bbox[0] / self.px_per_pt
            pdf_y = bbox[1] / self.px_per_pt
        else:
            pdf_x = word["x1"] + 5
            pdf_y = word["y0"]

        # FIX: сохраняем якорь + смещение — это и есть "шаблон"
        anchor_text = word.get("text", "")
        rule = {
            "image": element,
            "width": self.width_var.get(),
            # абсолютные координаты (fallback, привязка к текущей странице)
            "x": round(pdf_x, 2),
            "y": round(pdf_y, 2),
            # привязка к тексту (шаблон для batch_stamp)
            "anchor_text": anchor_text,
            "anchor_occurrence": occurrence,
            "offset_x": round(pdf_x - word["x0"], 2),
            "offset_y": round(pdf_y - word["y0"], 2),
            "page": 0,
        }
        self.rules.append(rule)
        print(f"✅ Правило: {rule}")

        self.canvas.delete("placement_elem")
        self.canvas.delete("placement_hit")
        self._placement_id = None
        if hasattr(self, "_placement_img_ref"):
            self._placement_img_ref = None
        if hasattr(self, "_control_frame"):
            self._control_frame.destroy()
            self._control_frame = None
        self._is_placing = False
        self._advance()

    def _cancel_placement(self):
        self.canvas.delete("placement_elem")
        self.canvas.delete("placement_hit")
        self._placement_id = None
        if hasattr(self, "_control_frame"):
            self._control_frame.destroy()
            self._control_frame = None
        self._is_placing = False
        self._advance()

    # ------------------------------------------------------ manual position
    def _ask_position_manual(self, element: str, word: dict):
        """Ручной ввод координат + перетаскивание в превью."""
        dlg = tk.Toplevel(self)
        dlg.title(f"Ручное размещение — {element}")
        dlg.geometry("500x400")
        dlg.transient(self)
        dlg.attributes("-topmost", True)
        self.after(50, lambda: dlg.grab_set())

        preview_frame = ttk.LabelFrame(dlg, text="Предпросмотр", padding=8)
        preview_frame.pack(fill="both", expand=True, padx=10, pady=5)

        preview_canvas = tk.Canvas(preview_frame, width=400, height=200, bg="#eee")
        preview_canvas.pack()

        preview_canvas.create_rectangle(5, 5, 395, 195, outline="#999", fill="#fff", tags="bg")
        preview_canvas.create_text(200, 18, text="PDF страница", fill="#999",
                                   font=("Arial", 8), tags="bg")

        pil_img = self._element_images.get(element)
        # Масштаб превью: вписываем страницу в 390x190 px
        preview_scale = min(390.0 / self.page_width, 190.0 / self.page_height)

        preview_tk = None
        if pil_img:
            width_pt = self.width_var.get()
            orig_w, orig_h = pil_img.size
            ratio = width_pt / orig_w
            height_pt = max(1, int(orig_h * ratio))
            pw = max(1, int(width_pt * preview_scale))
            ph = max(1, int(height_pt * preview_scale))
            preview_tk = ImageTk.PhotoImage(pil_img.resize((pw, ph), Image.LANCZOS))
            preview_canvas._img_ref = preview_tk
        else:
            preview_canvas.create_text(200, 100, text="Нет изображения", fill="#999")

        coord_frame = ttk.Frame(dlg, padding=(20, 4))
        coord_frame.pack(fill="x")

        x_var = tk.DoubleVar(value=float(word["x0"]))
        y_var = tk.DoubleVar(value=float(word["y0"]))

        ttk.Label(coord_frame, text="X:").pack(side="left")
        ttk.Entry(coord_frame, textvariable=x_var, width=12).pack(side="left", padx=4)
        ttk.Label(coord_frame, text="Y:").pack(side="left", padx=(12, 0))
        ttk.Entry(coord_frame, textvariable=y_var, width=12).pack(side="left", padx=4)

        def update_preview(*_):
            for tag in preview_canvas.find_withtag("elem"):
                preview_canvas.delete(tag)
            if preview_tk is not None:
                px = 5 + float(x_var.get()) * preview_scale
                py = 5 + float(y_var.get()) * preview_scale
                preview_canvas.create_image(px, py, anchor="nw",
                                            image=preview_tk, tags="elem")

        # FIX: перетаскивание обновляет x_var / y_var, а не только картинку
        def on_drag_start(event):
            items = preview_canvas.find_closest(event.x, event.y)
            if items and "elem" in preview_canvas.gettags(items[0]):
                preview_canvas._drag_data = {"x": event.x, "y": event.y}

        def on_drag(event):
            if not hasattr(preview_canvas, "_drag_data"):
                return
            dx = event.x - preview_canvas._drag_data["x"]
            dy = event.y - preview_canvas._drag_data["y"]
            preview_canvas._drag_data["x"] = event.x
            preview_canvas._drag_data["y"] = event.y
            x_var.set(float(x_var.get()) + dx / preview_scale)
            y_var.set(float(y_var.get()) + dy / preview_scale)

        preview_canvas.bind("<ButtonPress-1>", on_drag_start)
        preview_canvas.bind("<B1-Motion>", on_drag)

        x_var.trace_add("write", update_preview)
        y_var.trace_add("write", update_preview)
        update_preview()

        def confirm():
            px = float(x_var.get())
            py = float(y_var.get())
            nearest = self._find_nearest_word(px, py)
            if nearest:
                anchor_text = nearest["text"]
                occurrence = self._compute_occurrence(nearest)
                ox = px - nearest["x0"]
                oy = py - nearest["y0"]
            else:
                anchor_text, occurrence, ox, oy = "", 0, px, py

            self.rules.append({
                "image": element,
                "width": self.width_var.get(),
                "x": round(px, 2),
                "y": round(py, 2),
                "anchor_text": anchor_text,
                "anchor_occurrence": occurrence,
                "offset_x": round(ox, 2),
                "offset_y": round(oy, 2),
                "page": 0,
            })
            dlg.destroy()
            self._advance()

        ttk.Button(dlg, text="OK", command=confirm).pack(pady=10)

    # ---------------------------------------------------------------- save
    def _save_and_close(self):
        if not self.rules:
            if not messagebox.askyesno("Нет правил", "Ни один элемент не размещён. Выйти?"):
                return
            self.destroy()
            return

        config = {
            "background_threshold": 235,
            "rules": self.rules,
        }
        config_path = Path(__file__).resolve().parent / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        messagebox.showinfo(
            "Сохранено",
            f"Правил записано: {len(self.rules)}\nФайл: {config_path.name}",
        )
        self.destroy()


# ---------------------------------------------------------------- entry
def main():
    script_dir = Path(__file__).resolve().parent
    source_dir = script_dir / "source"
    config_path = script_dir / "config.json"

    if not source_dir.exists():
        messagebox.showerror(
            "Нет папки source",
            f"Папка {source_dir} не найдена.\nСоздайте её и положите туда PDF.",
        )
        sys.exit(1)

    root = tk.Tk()
    root.withdraw()

    default_sig, default_seal = get_existing_counts(config_path)
    if default_sig is None:
        default_sig = DEFAULT_SIGNATURES
    if default_seal is None:
        default_seal = DEFAULT_SEALS

    sig_count = simpledialog.askinteger(
        "Количество подписей", "Сколько подписей разместить?\n(от 0 до 20)",
        initialvalue=default_sig, minvalue=0, maxvalue=MAX_ELEMENTS, parent=root,
    )
    if sig_count is None:
        root.destroy()
        return

    seal_count = simpledialog.askinteger(
        "Количество печатей", "Сколько печатей разместить?\n(от 0 до 20)",
        initialvalue=default_seal, minvalue=0, maxvalue=MAX_ELEMENTS, parent=root,
    )
    if seal_count is None:
        root.destroy()
        return

    if sig_count + seal_count == 0:
        messagebox.showerror("Нет элементов",
                             "Нужно указать хотя бы одну подпись или одну печать.",
                             parent=root)
        root.destroy()
        return

    elements = build_elements(sig_count, seal_count)
    found = [e for e in elements if (script_dir / e).exists()]
    missing = [e for e in elements if not (script_dir / e).exists()]

    if not found:
        messagebox.showerror(
            "Файлы не найдены",
            "Ни один из файлов не найден:\n\n" + "\n".join(missing),
            parent=root,
        )
        root.destroy()
        return

    if missing:
        if not messagebox.askyesno(
            "Часть файлов отсутствует",
            "Не найдены:\n\n" + "\n".join(missing) + "\n\nПродолжить только с найденными?",
            parent=root,
        ):
            root.destroy()
            return
        elements = found

    if config_path.exists():
        if not messagebox.askyesno(
            "Настройки уже есть",
            "Файл config.json уже существует.\nПерезаписать?",
            parent=root,
        ):
            root.destroy()
            return

    pdfs = sorted(source_dir.glob("*.pdf"))
    if not pdfs:
        messagebox.showerror(
            "Нет PDF",
            f"В папке {source_dir.name}/ нет PDF-файлов.",
            parent=root,
        )
        root.destroy()
        return

    if len(pdfs) == 1:
        sample = pdfs[0]
    else:
        chosen = filedialog.askopenfilename(
            title="Выберите PDF-образец для настройки",
            initialdir=str(source_dir),
            filetypes=[("PDF", "*.pdf")], parent=root,
        )
        if not chosen:
            root.destroy()
            return
        sample = Path(chosen)

    root.destroy()

    print(f"Открываю для настройки: {sample.name}")
    app = TextAnchorDialog(sample, elements)
    app.mainloop()


if __name__ == "__main__":
    main()
# Сторонние компоненты и происхождение

## Модели в архиве

- `models/yolov8n.onnx`: экспорт официальных весов Ultralytics YOLOv8n COCO. Исходный файл получен из https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt . Экспорт: ONNX opset 17, вход 1×3×640×640, без встроенного NMS и динамических размеров. Экспорт выполнен Ultralytics 8.4.171. Лицензия Ultralytics AGPL-3.0: `licenses/AGPL-3.0.txt`.
- `models/yolov8n-face.onnx`: неизменённый ONNX из авторского релиза https://github.com/akanametov/yolo-face/releases/download/1.0.0/yolov8n-face.onnx . Это отдельная модель с классом `face`, обученная на WIDERFace согласно встроенным метаданным; COCO-класс `person` не подменяет лицо. Метаданные: Ultralytics 8.3.241, YOLOv8n-pose, вход 1×3×640×640, выход 1×300×21, встроенный NMS, 5 лицевых ключевых точек. Для взгляда используются ориентиры MediaPipe, а не эти 5 точек. **Веса указывают AGPL-3.0 в ONNX metadata**, текст включён в `licenses/AGPL-3.0.txt`; репозиторий автора содержит **GPL-3.0**, его точный текст сохранён как `models/LICENSE-yolo-face.txt`. Исходники автора и инструкции обучения/экспорта: https://github.com/akanametov/yolo-face/tree/1.0.0 . Обе исходные атрибуции сохранены; лицензия модели не объявляется разрешительной.
- `models/face_landmarker.task`: официальный пакет Google MediaPipe: https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task . Документация модели: https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker . Код MediaPipe использует Apache-2.0; текст включён в `licenses/APACHE-2.0.txt`. Условия исходного пакета и model card Google сохраняют силу.

Контрольные суммы конкретных файлов: `models/manifest.json`.

## Устанавливаемые зависимости

FastAPI и pywebview — MIT; ONNX Runtime — MIT; MediaPipe — Apache-2.0; OpenCV — Apache-2.0; NumPy — BSD-3-Clause; Uvicorn и Starlette — BSD-3-Clause. Полные метаданные и уведомления зависимостей устанавливаются вместе с Python-пакетами. Номера версий закреплены в requirements-файлах.

Исходный код Qorgau в этом архиве предоставлен под AGPL-3.0. Текст — в `LICENSE`. Сторонние компоненты сохраняют собственные авторские права и лицензии.

Веб-интерфейс, SVG-иллюстрация и учебные вопросы созданы для этого проекта. Внешние фотографии и COCO128, использованные только для проверки, в архив не включены.

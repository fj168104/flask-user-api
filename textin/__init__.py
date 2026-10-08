"""TextIn OCR provider for flask-user-api.

Exposes a Flask blueprint mounted at /api/v1/ocr that proxies document
OCR calls to the TextIn xparse API, persists records to local JSON,
and returns the target project's {code, msg, data} envelope.
"""
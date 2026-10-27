"""Test access to one Canvas file, keeping downloaded bytes in memory only."""

import argparse

import requests

from backend.app.canvas_service import download_canvas_file


def test_file_download(course_id, file_id, extract=False):
    metadata, content = download_canvas_file(course_id, file_id)
    print(f"File name: {metadata.get('display_name') or metadata.get('filename') or 'Unknown'}")
    print(f"Content type: {metadata.get('content-type') or metadata.get('content_type') or 'Unknown'}")
    print(f"File size: {metadata.get('size', 'Unknown')} bytes")
    print("Download succeeded: yes")
    print(f"Bytes downloaded: {len(content)}")
    if extract:
        from backend.app.document_service import extract_file
        result = extract_file(metadata, content)
        print(f"Text extraction: {result['status']}")
        print(f"Format: {result['format']}")
        if result.get("slide_count") is not None:
            print(f"Slides: {result['slide_count']}")
            print(f"Slides with incomplete text: {sum(slide['status'] != 'ok' for slide in result['slides'])}")
        elif result.get("blocks") is not None:
            print(f"Document blocks: {len(result['blocks'])}")
        elif result.get("cells") is not None:
            print(f"Notebook cells: {len(result['cells'])}")
        else:
            print(f"Pages: {result['page_count']}")
            print(f"Pages without text: {sum(page['status'] != 'ok' for page in result['pages'])}")
        print(f"Text characters: {result['character_count']}")
        warnings = set(result.get("warnings", []))
        for slide in result.get("slides", []):
            warnings.update(slide.get("warnings", []))
        if warnings:
            print("Extraction gaps: " + ", ".join(sorted(warnings)))
        return result["status"] in ("ok", "partial")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("course_id", type=int)
    parser.add_argument("file_id", type=int)
    parser.add_argument("--extract", action="store_true", help="Also verify supported text extraction; nothing is saved.")
    args = parser.parse_args()
    try:
        if not test_file_download(args.course_id, args.file_id, args.extract):
            parser.exit(1, "The file downloaded, but usable text was not extracted.\n")
    except (requests.RequestException, ValueError) as error:
        # Don't print request URLs: they can contain temporary signed credentials.
        print("Download succeeded: no")
        print("Bytes downloaded: 0")
        if isinstance(error, requests.RequestException):
            status = error.response.status_code if error.response is not None else type(error).__name__
            parser.exit(1, f"File access/download failed: {status}\n")
        parser.exit(1, f"File access/download failed: {error}\n")

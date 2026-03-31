"""
워커 릴리즈 배포 스크립트
사용법: python3 deploy.py <버전> "<변경내역>"
예시:   python3 deploy.py 0.3.0 "블로그 핸들러 개선, 에러 처리 보강"
"""
import json
import os
import sys
import urllib.request

STATION_URL = os.environ.get(
    "CRAWLSTATION_URL", "https://crawl-station.vercel.app"
)

WORKER_FILES = [
    "worker.py",
    "handlers/__init__.py",
    "handlers/base.py",
    "handlers/blog.py",
    "handlers/serp.py",
    "handlers/kin.py",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) < 2:
        print("사용법: python3 deploy.py <버전> [변경내역]")
        print('예시:   python3 deploy.py 0.3.0 "블로그 핸들러 개선"')
        sys.exit(1)

    version = sys.argv[1]
    changelog = sys.argv[2] if len(sys.argv) > 2 else ""

    # 파일 수집
    files = {}
    for f in WORKER_FILES:
        path = os.path.join(BASE_DIR, f)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                files[f] = fh.read()
            print(f"  ✅ {f}")
        else:
            print(f"  ⚠️ {f} 없음 — 스킵")

    if not files:
        print("❌ 배포할 파일이 없습니다")
        sys.exit(1)

    # worker.py 버전 확인
    if "worker.py" in files:
        for line in files["worker.py"].splitlines():
            if line.startswith("VERSION"):
                file_version = line.split('"')[1]
                if file_version != version:
                    print(f"\n⚠️ worker.py VERSION이 '{file_version}'인데 배포 버전은 '{version}'입니다")
                    answer = input("   계속하시겠습니까? (y/N): ").strip().lower()
                    if answer != "y":
                        sys.exit(0)
                break

    # 릴리즈 등록
    payload = json.dumps({
        "version": version,
        "changelog": changelog,
        "files": files,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{STATION_URL}/api/releases",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"\n📦 v{version} 배포 중... ({len(files)}개 파일)")

    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.loads(res.read())
            print(f"✅ {data['message']}")
            print(f"   업데이트 대기 워커: {data.get('outdated_workers', 0)}대")
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        print(f"❌ 배포 실패: {body.get('error', e)}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 연결 실패: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

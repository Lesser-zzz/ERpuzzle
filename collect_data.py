```python
import requests
import csv
import time
import os
from collections import deque
from urllib.parse import quote


# ============================================================
# 설정
# ============================================================

# GitHub Actions / 실행 환경에서 반드시 BSER_API_KEY를 넣어주세요.
API_KEY = os.environ.get("BSER_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "❌ BSER_API_KEY 환경변수를 찾을 수 없습니다.\n"
        "GitHub Actions에서는 secrets.BSER_API_KEY를 env로 전달해주세요."
    )

HEADERS = {
    "x-api-key": API_KEY,
    "accept": "application/json",
}

# 기존 데이터셋 그대로 사용
CSV_FILENAME = "er_makgeumgu_dataset_v2.csv"

# ============================================================
# Snowball 상태 파일
# ============================================================

PENDING_FILE = "snowball_pending_add.txt"
PROCESSED_FILE = "snowball_processed.txt"
KNOWN_GAMES_FILE = "known_games.txt"

# ============================================================
# 수집 설정
# ============================================================

# 현재 사용 중인 시즌
SEASON_ID = 41

# 3 = Squad
MATCHING_MODE = 3

# 최초 Seed 유저 수
INITIAL_SEED_COUNT = 100

# 유저 1명당 확인할 최근 게임
RECENT_GAME_LIMIT = 20

# 1회 실행당 최대 탐색 유저 수
MAX_LOOPS = 20

# API 요청 간 최소 간격
REQUEST_INTERVAL = 1.1

# Rate Limit 발생 시 대기
RATE_LIMIT_WAIT = 10

# 네트워크 오류 시 대기
NETWORK_ERROR_WAIT = 5


# ============================================================
# API 요청
# ============================================================

last_request_time = 0.0


def api_get(url):
    """
    모든 API 요청을 이 함수로 통과시켜
    요청 간격을 일정하게 유지합니다.
    """

    global last_request_time

    while True:
        elapsed = time.time() - last_request_time

        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=20,
            )

            last_request_time = time.time()

            # Rate Limit
            if response.status_code in (403, 429):
                print(
                    f"   ⚠️ Rate Limit 감지 "
                    f"({response.status_code})"
                )
                print(
                    f"   → {RATE_LIMIT_WAIT}초 대기 후 재시도합니다."
                )

                time.sleep(RATE_LIMIT_WAIT)
                continue

            return response

        except requests.RequestException as e:
            last_request_time = time.time()

            print(
                f"   ⚠️ 네트워크 오류: {e}"
            )
            print(
                f"   → {NETWORK_ERROR_WAIT}초 후 재시도합니다."
            )

            time.sleep(NETWORK_ERROR_WAIT)


# ============================================================
# 텍스트 상태 파일 처리
# ============================================================

def load_lines(filename):
    """
    한 줄에 하나씩 저장된 값을 set으로 불러옵니다.
    """

    if not os.path.exists(filename):
        return set()

    result = set()

    with open(
        filename,
        "r",
        encoding="utf-8",
    ) as f:

        for line in f:
            value = line.strip()

            if value:
                result.add(value)

    return result


def append_line(filename, value):
    """
    파일 마지막에 한 줄 추가
    """

    with open(
        filename,
        "a",
        encoding="utf-8",
    ) as f:

        f.write(f"{value}\n")


# ============================================================
# CSV 초기화 / 기존 데이터 로드
# ============================================================

processed_game_ids = set()


if os.path.exists(CSV_FILENAME):

    with open(
        CSV_FILENAME,
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        reader = csv.reader(f)

        # 헤더
        next(reader, None)

        for row in reader:

            if not row:
                continue

            try:
                gid = int(row[0])
                processed_game_ids.add(gid)

            except (ValueError, TypeError):
                pass

else:

    print(
        f"ℹ️ {CSV_FILENAME}이 존재하지 않아 새로 생성합니다."
    )

    with open(
        CSV_FILENAME,
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        writer = csv.writer(f)

        header = ["game_id"]

        for prefix in ["win", "lose"]:
            for i in range(1, 4):

                header.extend([
                    f"{prefix}_char{i}",
                    f"{prefix}_lv{i}",
                    f"{prefix}_leg{i}",
                    f"{prefix}_tran{i}",
                ])

        writer.writerow(header)


# ============================================================
# 상태 파일 로드
# ============================================================

processed_nicknames = load_lines(PROCESSED_FILE)
pending_nicknames = load_lines(PENDING_FILE)
known_games = load_lines(KNOWN_GAMES_FILE)

# 이미 처리한 유저는 pending에서 제거
pending_nicknames -= processed_nicknames


# ============================================================
# 시작 상태 출력
# ============================================================

print()
print("=" * 65)
print("🚀 Eternal Return Snowball Collector")
print("=" * 65)

print(
    f"현재 CSV 데이터       : "
    f"{len(processed_game_ids):,} 게임"
)

print(
    f"상세 조회 완료 게임   : "
    f"{len(known_games):,} 게임"
)

print(
    f"탐색 완료 유저        : "
    f"{len(processed_nicknames):,} 명"
)

print(
    f"탐색 대기 유저        : "
    f"{len(pending_nicknames):,} 명"
)


# ============================================================
# 최초 Seed 확보
# ============================================================

if (
    not pending_nicknames
    and not processed_nicknames
):

    print()
    print(
        f"🌱 최초 실행 → "
        f"랭킹 상위 {INITIAL_SEED_COUNT}명 확보"
    )

    url_rank = (
        "https://open-api.bser.io/v1/rank/top/"
        f"{SEASON_ID}/{INITIAL_SEED_COUNT}"
    )

    response_rank = api_get(url_rank)

    if response_rank.status_code != 200:

        raise RuntimeError(
            f"랭킹 API 실패: "
            f"{response_rank.status_code}"
        )

    try:
        rank_json = response_rank.json()

    except Exception as e:

        raise RuntimeError(
            f"랭킹 API JSON 파싱 실패: {e}"
        )

    top_ranks = rank_json.get("topRanks", [])

    for player in top_ranks[:INITIAL_SEED_COUNT]:

        nickname = player.get("nickname")

        if not nickname:
            continue

        if (
            nickname not in processed_nicknames
            and nickname not in pending_nicknames
        ):

            pending_nicknames.add(nickname)

            append_line(
                PENDING_FILE,
                nickname,
            )

    print(
        f"✅ Seed 확보 완료: "
        f"{len(pending_nicknames):,}명"
    )


# ============================================================
# 작업 큐
# ============================================================

queue = deque()

for nickname in pending_nicknames:
    queue.append(nickname)


# ============================================================
# Snowball Loop
# ============================================================

loop_count = 0


while queue and loop_count < MAX_LOOPS:

    nickname = queue.popleft()

    # 이미 처리했다면 스킵
    if nickname in processed_nicknames:
        continue

    loop_count += 1

    print()
    print(
        "=" * 65
    )
    print(
        f"🔍 [{loop_count}/{MAX_LOOPS}] "
        f"'{nickname}' 탐색"
    )
    print(
        f"현재 데이터셋: "
        f"{len(processed_game_ids):,} 게임"
    )
    print(
        f"남은 대기 유저: "
        f"{len(queue):,} 명"
    )


    # ========================================================
    # 1. 닉네임 → UID
    # ========================================================

    encoded_nickname = quote(
        nickname,
        safe="",
    )

    url_user = (
        "https://open-api.bser.io/v1/user/nickname"
        f"?query={encoded_nickname}"
    )

    res_user = api_get(url_user)

    if res_user.status_code != 200:

        print(
            f"   ❌ 유저 조회 실패 "
            f"(HTTP {res_user.status_code})"
        )

        # 존재하지 않는 유저 / API 오류 구분이 어려우므로
        # 일단 이번 사이클에서는 처리 완료 처리
        processed_nicknames.add(nickname)

        append_line(
            PROCESSED_FILE,
            nickname,
        )

        continue


    try:
        user_json = res_user.json()

    except Exception:

        print(
            "   ❌ 유저 API JSON 파싱 실패"
        )

        processed_nicknames.add(nickname)

        append_line(
            PROCESSED_FILE,
            nickname,
        )

        continue


    user_data = user_json.get("user")

    if not user_data:

        print(
            "   ❌ 유저 정보가 없습니다."
        )

        processed_nicknames.add(nickname)

        append_line(
            PROCESSED_FILE,
            nickname,
        )

        continue


    user_id = (
        user_data.get("uid")
        or user_data.get("userId")
    )

    if not user_id:

        print(
            "   ❌ userId/uid를 찾지 못했습니다."
        )

        processed_nicknames.add(nickname)

        append_line(
            PROCESSED_FILE,
            nickname,
        )

        continue


    # ========================================================
    # 2. 최근 게임 조회
    # ========================================================

    url_games = (
        "https://open-api.bser.io/v1/user/games/uid/"
        f"{user_id}"
    )

    res_games = api_get(url_games)

    if res_games.status_code != 200:

        print(
            f"   ❌ 게임 목록 조회 실패 "
            f"(HTTP {res_games.status_code})"
        )

        processed_nicknames.add(nickname)

        append_line(
            PROCESSED_FILE,
            nickname,
        )

        continue


    try:
        games_json = res_games.json()

    except Exception:

        print(
            "   ❌ 게임 목록 JSON 파싱 실패"
        )

        processed_nicknames.add(nickname)

        append_line(
            PROCESSED_FILE,
            nickname,
        )

        continue


    games_data = games_json.get(
        "userGames",
        [],
    )


    if not games_data:

        print(
            "   ℹ️ 최근 게임 기록 없음"
        )

        processed_nicknames.add(nickname)

        append_line(
            PROCESSED_FILE,
            nickname,
        )

        continue


    # ========================================================
    # 3. 최근 게임 ID 추출
    #
    # set()으로 바로 변환하지 않고
    # API가 준 순서를 유지합니다.
    # ========================================================

    game_id_list = []
    seen_game_ids = set()

    for game in games_data:

        gid = game.get("gameId")

        if gid is None:
            continue

        if gid in seen_game_ids:
            continue

        seen_game_ids.add(gid)
        game_id_list.append(gid)

        if len(game_id_list) >= RECENT_GAME_LIMIT:
            break


    print(
        f"   최근 게임 {len(game_id_list)}개 확인"
    )


    # ========================================================
    # 이번 유저가 만난 게임 처리
    # ========================================================

    for gid in game_id_list:

        gid_str = str(gid)

        # ----------------------------------------------------
        # 이미 상세 조회에 성공한 게임
        # ----------------------------------------------------

        if gid_str in known_games:

            continue


        # ----------------------------------------------------
        # 게임 상세 API
        # ----------------------------------------------------

        url_detail = (
            f"https://open-api.bser.io/v1/games/{gid}"
        )

        res_detail = api_get(url_detail)

        if res_detail.status_code != 200:

            print(
                f"   ⚠️ 게임 {gid} 상세 조회 실패 "
                f"(HTTP {res_detail.status_code})"
            )

            # 중요:
            # known_games에는 넣지 않습니다.
            # 나중에 다시 만나면 재시도합니다.
            continue


        try:
            detail_json = res_detail.json()

        except Exception:

            print(
                f"   ⚠️ 게임 {gid} JSON 파싱 실패"
            )

            # 역시 known_games에 기록하지 않음
            continue


        detail_data = detail_json.get(
            "userGames",
            [],
        )


        if not detail_data:

            print(
                f"   ⚠️ 게임 {gid}: 참가자 데이터 없음"
            )

            continue


        # ====================================================
        # 참가자 닉네임 → Snowball 추가
        #
        # CSV 저장 성공 여부와 관계없이
        # 새 닉네임은 여기서 확보합니다.
        # ====================================================

        discovered_count = 0

        for player in detail_data:

            new_nick = player.get("nickname")

            if not new_nick:
                continue

            if (
                new_nick not in processed_nicknames
                and new_nick not in pending_nicknames
            ):

                pending_nicknames.add(
                    new_nick
                )

                queue.append(
                    new_nick
                )

                append_line(
                    PENDING_FILE,
                    new_nick
                )

                discovered_count += 1


        # ====================================================
        # 1위 / 2위 팀 데이터 추출
        # ====================================================

        final_teams = {
            1: [],
            2: [],
        }

        rank_counts = {
            1: 0,
            2: 0,
        }


        for player in detail_data:

            rank = player.get("gameRank")

            if rank not in (1, 2):
                continue


            rank_counts[rank] += 1


            equip_grades = player.get(
                "equipmentGrade",
                {}
            )

            if not isinstance(
                equip_grades,
                dict
            ):
                equip_grades = {}


            leg_cnt = sum(
                1
                for grade in equip_grades.values()
                if grade == 5
            )

            tran_cnt = sum(
                1
                for grade in equip_grades.values()
                if grade == 6
            )


            final_teams[rank].extend([
                player.get("characterNum"),
                player.get("characterLevel"),
                leg_cnt,
                tran_cnt,
            ])


        # ====================================================
        # 데이터 완전성 검사
        # ====================================================

        team1_count = rank_counts[1]
        team2_count = rank_counts[2]


        if (
            team1_count == 3
            and team2_count == 3
            and len(final_teams[1]) == 12
            and len(final_teams[2]) == 12
        ):

            # ------------------------------------------------
            # CSV 저장
            # ------------------------------------------------

            if gid not in processed_game_ids:

                with open(
                    CSV_FILENAME,
                    "a",
                    encoding="utf-8-sig",
                    newline="",
                ) as f:

                    writer = csv.writer(f)

                    writer.writerow(
                        [gid]
                        + final_teams[1]
                        + final_teams[2]
                    )


                processed_game_ids.add(gid)


            # ------------------------------------------------
            # ⭐ 핵심 수정
            #
            # CSV 저장이 성공한 경우에만
            # known_games에 등록합니다.
            # ------------------------------------------------

            known_games.add(gid_str)

            append_line(
                KNOWN_GAMES_FILE,
                gid_str,
            )


            print(
                f"   ✅ 게임 {gid} 저장 완료 "
                f"| 새 유저 +{discovered_count} "
                f"| 전체 {len(processed_game_ids):,}"
            )


        else:

            # ------------------------------------------------
            # 저장 실패
            # ------------------------------------------------

            print(
                f"   ⚠️ 게임 {gid}: "
                f"1/2위 데이터가 완전하지 않음"
            )

            print(
                f"      1위 참가자: {team1_count}/3"
            )

            print(
                f"      2위 참가자: {team2_count}/3"
            )

            print(
                f"      새 유저: +{discovered_count}"
            )

            print(
                "      → CSV 저장 안 함"
            )

            print(
                "      → known_games에도 기록 안 함"
            )

            print(
                "      → 나중에 다시 발견하면 재시도"
            )


        # ----------------------------------------------------
        # API 보호
        #
        # api_get 자체에서도 간격을 관리하지만
        # 루프의 가독성을 위해 별도 sleep은 하지 않습니다.
        # ----------------------------------------------------


    # ========================================================
    # 현재 유저 처리 완료
    # ========================================================

    processed_nicknames.add(
        nickname
    )

    append_line(
        PROCESSED_FILE,
        nickname
    )

    pending_nicknames.discard(
        nickname
    )


    print(
        f"   📈 현재 상태 "
        f"| 데이터 {len(processed_game_ids):,} "
        f"| 처리 유저 {len(processed_nicknames):,} "
        f"| 대기 큐 {len(queue):,}"
    )


# ============================================================
# 종료
# ============================================================

print()
print("=" * 65)
print("🎉 이번 수집 사이클 종료")
print("=" * 65)

print(
    f"이번 실행 탐색 유저 : "
    f"{loop_count:,}명"
)

print(
    f"현재 데이터셋       : "
    f"{len(processed_game_ids):,} 게임"
)

print(
    f"상세 조회 완료 게임 : "
    f"{len(known_games):,} 게임"
)

print(
    f"탐색 완료 유저      : "
    f"{len(processed_nicknames):,} 명"
)

print(
    f"탐색 대기 유저      : "
    f"{len(queue):,} 명"
)

print()
print(
    "💾 상태 파일이 저장되어 있으므로 "
    "다시 실행하면 이어서 수집합니다."
)
```

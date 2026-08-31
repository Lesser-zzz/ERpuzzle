import requests
import csv
import time
import os
from collections import deque

# ============================================================
# 설정
# ============================================================

API_KEY = "6ws22XYyOd4F0Puup5Hps2eK0XZ5uHMC3kqKIrYL"
HEADERS = {
    "x-api-key": API_KEY,
    "accept": "application/json"
}

CSV_FILENAME = "er_makgeumgu_dataset_v2.csv"

# 스노우볼 상태 저장 파일
PENDING_ADD_FILE = "snowball_pending_add.txt"
PROCESSED_FILE = "snowball_processed.txt"
KNOWN_GAMES_FILE = "known_games.txt"

# 현재 사용 중인 시즌 / 모드
SEASON_ID = 41
MATCHING_TEAM_MODE = 3  # Squad

# 처음 시작할 때 랭킹 Seed를 몇 명 넣을지
INITIAL_SEED_COUNT = 100

# 유저 1명당 확인할 최근 게임 수
RECENT_GAME_LIMIT = 20

# 한 번 실행할 때 최대 탐색할 유저 수
MAX_LOOPS = 50

# API 요청 간격
# 공식 문서에는 403/429 rate limit 응답이 존재하므로
# 1초보다 약간 여유를 둬서 1.1초 사용
REQUEST_INTERVAL = 1.1

# ============================================================
# API 요청 제어
# ============================================================

last_request_time = 0.0


def api_get(url):
    """
    모든 API 요청을 이 함수 하나로 통과시켜서
    최소 REQUEST_INTERVAL초의 간격을 보장합니다.
    """

    global last_request_time

    elapsed = time.time() - last_request_time

    if elapsed < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - elapsed)

    while True:
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=20
            )

            last_request_time = time.time()

            # Rate Limit
            if response.status_code in (403, 429):
                print(
                    f"   ⚠️ Rate Limit 감지 ({response.status_code}) "
                    f"→ 10초 대기 후 재시도합니다."
                )
                time.sleep(10)
                continue

            return response

        except requests.RequestException as e:
            print(f"   ⚠️ 네트워크 오류: {e}")
            print("   → 5초 후 재시도합니다.")
            time.sleep(5)


# ============================================================
# 파일 로드
# ============================================================

def load_lines(filename):
    """
    한 줄에 하나씩 저장된 텍스트 파일을 set으로 읽습니다.
    """

    if not os.path.exists(filename):
        return set()

    result = set()

    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            value = line.strip()

            if value:
                result.add(value)

    return result


def append_line(filename, value):
    """
    파일 끝에 한 줄 추가
    """

    with open(filename, "a", encoding="utf-8") as f:
        f.write(str(value) + "\n")


# ============================================================
# 기존 CSV 게임 ID 로드
# ============================================================

processed_game_ids = set()

if os.path.exists(CSV_FILENAME):
    with open(
        CSV_FILENAME,
        mode="r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.reader(f)

        next(reader, None)

        for row in reader:
            if row and row[0]:
                try:
                    processed_game_ids.add(int(row[0]))
                except ValueError:
                    pass

else:
    with open(
        CSV_FILENAME,
        mode="w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.writer(f)

        header = ["game_id"]

        for prefix in ["win", "lose"]:
            for i in range(1, 4):
                header.extend([
                    f"{prefix}_char{i}",
                    f"{prefix}_lv{i}",
                    f"{prefix}_leg{i}",
                    f"{prefix}_tran{i}"
                ])

        writer.writerow(header)


print("=" * 60)
print("🚀 Eternal Return Snowball Collector v3")
print("=" * 60)
print(f"현재 CSV 데이터: {len(processed_game_ids):,}게임")


# ============================================================
# 스노우볼 상태 로드
# ============================================================

processed_nicknames = load_lines(PROCESSED_FILE)
pending_nicknames = load_lines(PENDING_ADD_FILE)
known_games = load_lines(KNOWN_GAMES_FILE)

# processed 된 유저는 pending에서 제외
pending_nicknames -= processed_nicknames

print(f"탐색 완료 유저: {len(processed_nicknames):,}명")
print(f"탐색 대기 유저: {len(pending_nicknames):,}명")
print(f"상세 조회 완료 게임: {len(known_games):,}개")


# ============================================================
# 첫 실행이라면 Seed를 확보
# ============================================================

if not pending_nicknames and not processed_nicknames:

    print()
    print("🌱 최초 실행 감지")
    print(f"랭킹 상위 {INITIAL_SEED_COUNT}명을 Seed로 확보합니다...")

    url_rank = (
        f"https://open-api.bser.io/v1/rank/top/"
        f"{SEASON_ID}/{MATCHING_TEAM_MODE}"
    )

    response_rank = api_get(url_rank)

    if response_rank.status_code == 200:

        rank_data = response_rank.json()

        top_ranks = rank_data.get("topRanks", [])

        for player in top_ranks[:INITIAL_SEED_COUNT]:

            nickname = player.get("nickname")

            if not nickname:
                continue

            if nickname not in processed_nicknames:
                if nickname not in pending_nicknames:

                    pending_nicknames.add(nickname)
                    append_line(PENDING_ADD_FILE, nickname)

        print(
            f"✅ Seed 확보 완료: "
            f"{len(pending_nicknames):,}명"
        )

    else:

        print(
            f"❌ 랭킹 API 실패: "
            f"{response_rank.status_code}"
        )

        raise SystemExit


# ============================================================
# 작업 큐
# ============================================================

queue = deque(pending_nicknames)

loop_count = 0


# ============================================================
# 핵심 Snowball Loop
# ============================================================

while queue and loop_count < MAX_LOOPS:

    nickname = queue.popleft()

    # 이미 처리된 경우
    if nickname in processed_nicknames:
        continue

    loop_count += 1

    print()
    print(
        f"🔍 [{loop_count}/{MAX_LOOPS}] "
        f"'{nickname}' 탐색 시작"
    )

    # --------------------------------------------------------
    # 1. 닉네임 → UID
    # --------------------------------------------------------

    url_user = (
        "https://open-api.bser.io/v1/user/nickname"
        f"?query={requests.utils.quote(nickname)}"
    )

    res_user = api_get(url_user)

    if res_user.status_code != 200:

        print(
            f"   ❌ 유저 조회 실패 "
            f"({res_user.status_code})"
        )

        # 실패한 유저도 일단 processed 처리
        processed_nicknames.add(nickname)
        append_line(PROCESSED_FILE, nickname)

        continue

    try:
        user_json = res_user.json()
    except Exception:
        print("   ❌ 유저 API JSON 파싱 실패")

        processed_nicknames.add(nickname)
        append_line(PROCESSED_FILE, nickname)

        continue

    user_data = user_json.get("user")

    if not user_data:
        print("   ❌ 존재하지 않는 유저")

        processed_nicknames.add(nickname)
        append_line(PROCESSED_FILE, nickname)

        continue

    # 현재 API 응답에서는 uid / userId가 환경에 따라 다를 수 있으므로
    # 둘 다 대응
    user_id = user_data.get("uid") or user_data.get("userId")

    if not user_id:

        print("   ❌ UID를 찾지 못했습니다.")

        processed_nicknames.add(nickname)
        append_line(PROCESSED_FILE, nickname)

        continue

    # --------------------------------------------------------
    # 2. 최근 매치 조회
    # --------------------------------------------------------

    url_games = (
        "https://open-api.bser.io/v1/user/games/uid/"
        f"{user_id}"
    )

    res_games = api_get(url_games)

    if res_games.status_code != 200:

        print(
            f"   ❌ 게임 목록 조회 실패 "
            f"({res_games.status_code})"
        )

        processed_nicknames.add(nickname)
        append_line(PROCESSED_FILE, nickname)

        continue

    try:
        games_json = res_games.json()
    except Exception:

        print("   ❌ 게임 목록 JSON 파싱 실패")

        processed_nicknames.add(nickname)
        append_line(PROCESSED_FILE, nickname)

        continue

    games_data = games_json.get("userGames", [])

    if not games_data:

        print("   ℹ️ 최근 게임 기록 없음")

        processed_nicknames.add(nickname)
        append_line(PROCESSED_FILE, nickname)

        continue


    # --------------------------------------------------------
    # 3. 최근 게임에서 중복 gameId 제거
    #    set()을 사용하지 않고 API가 준 순서를 유지
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # 4. 개별 게임 상세 조회
    # --------------------------------------------------------

    for gid in game_id_list:

        gid_str = str(gid)

        # ----------------------------------------------------
        # 이미 상세 조회한 게임이면 완전히 스킵
        #
        # 핵심:
        # CSV에 있는 게임이라고 바로 스킵하지 않는다.
        #
        # 기존 1200게임은 known_games.txt에 없기 때문에
        # 첫 실행에서 다시 상세 조회하여 참가자를 확보한다.
        # ----------------------------------------------------

        if gid_str in known_games:

            continue


        url_detail = (
            f"https://open-api.bser.io/v1/games/{gid}"
        )

        res_detail = api_get(url_detail)

        if res_detail.status_code != 200:

            print(
                f"   ⚠️ 게임 {gid} 상세 조회 실패 "
                f"({res_detail.status_code})"
            )

            continue


        try:
            detail_json = res_detail.json()
        except Exception:

            print(
                f"   ⚠️ 게임 {gid} JSON 파싱 실패"
            )

            continue


        detail_data = detail_json.get("userGames", [])

        if not detail_data:

            print(
                f"   ⚠️ 게임 {gid} 참가자 정보 없음"
            )

            continue


        # ----------------------------------------------------
        # 5. 같은 게임의 모든 참가자를 Snowball에 추가
        # ----------------------------------------------------

        discovered_count = 0

        for p in detail_data:

            new_nick = p.get("nickname")

            if not new_nick:
                continue

            if (
                new_nick not in processed_nicknames
                and new_nick not in pending_nicknames
            ):

                pending_nicknames.add(new_nick)
                queue.append(new_nick)

                append_line(
                    PENDING_ADD_FILE,
                    new_nick
                )

                discovered_count += 1


        # ----------------------------------------------------
        # 6. 기존 CSV에 없는 경우 데이터 저장
        # ----------------------------------------------------

        final_teams = {
            1: [],
            2: []
        }

        for p in detail_data:

            rank = p.get("gameRank")

            if rank not in [1, 2]:
                continue


            equip_grades = p.get(
                "equipmentGrade",
                {}
            )

            # 기존 데이터 구조 유지
            leg_cnt = sum(
                1
                for g in equip_grades.values()
                if g == 5
            )

            tran_cnt = sum(
                1
                for g in equip_grades.values()
                if g == 6
            )


            final_teams[rank].extend([
                p.get("characterNum"),
                p.get("characterLevel"),
                leg_cnt,
                tran_cnt
            ])


        # ----------------------------------------------------
        # 7. 데이터셋 저장
        # ----------------------------------------------------

        if (
            len(final_teams[1]) == 12
            and len(final_teams[2]) == 12
        ):

            if gid not in processed_game_ids:

                with open(
                    CSV_FILENAME,
                    mode="a",
                    encoding="utf-8-sig",
                    newline=""
                ) as f:

                    writer = csv.writer(f)

                    writer.writerow([
                        gid
                    ]
                    + final_teams[1]
                    + final_teams[2])


                processed_game_ids.add(gid)

                print(
                    f"   ✅ 게임 {gid} 저장 완료 "
                    f"| 새 유저 +{discovered_count} "
                    f"| 전체 {len(processed_game_ids):,}"
                )

            else:

                print(
                    f"   ♻️ 게임 {gid}는 CSV에 이미 존재 "
                    f"| 새 유저 +{discovered_count}"
                )

        else:

            print(
                f"   ⚠️ 게임 {gid}: "
                f"1/2위 데이터가 완전하지 않아 "
                f"CSV 저장은 스킵 "
                f"| 새 유저 +{discovered_count}"
            )


        # ----------------------------------------------------
        # 8. 상세 조회 완료 기록
        #
        # CSV 저장 여부와 관계없이 기록한다.
        # 그래야 동일한 게임을 계속 재조회하지 않는다.
        # ----------------------------------------------------

        known_games.add(gid_str)
        append_line(
            KNOWN_GAMES_FILE,
            gid_str
        )


    # --------------------------------------------------------
    # 9. 현재 유저 탐색 완료 처리
    # --------------------------------------------------------

    processed_nicknames.add(nickname)
    append_line(
        PROCESSED_FILE,
        nickname
    )

    # pending set에서도 제거
    pending_nicknames.discard(nickname)


    print(
        f"   📈 현재 상태 "
        f"| 데이터 {len(processed_game_ids):,} "
        f"| 대기 유저 {len(queue):,} "
        f"| 완료 유저 {len(processed_nicknames):,}"
    )


# ============================================================
# 종료
# ============================================================

print()
print("=" * 60)
print("🎉 이번 수집 사이클 종료")
print("=" * 60)

print(
    f"이번 실행에서 탐색한 유저: "
    f"{loop_count:,}명"
)

print(
    f"현재 데이터셋: "
    f"{len(processed_game_ids):,}게임"
)

print(
    f"현재 탐색 완료 유저: "
    f"{len(processed_nicknames):,}명"
)

print(
    f"현재 대기 유저: "
    f"{len(queue):,}명"
)

print()
print(
    "다시 실행하면 snowball_pending_add.txt와 "
    "snowball_processed.txt를 기반으로 이어서 수집합니다."
)

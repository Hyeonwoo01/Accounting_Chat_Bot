# 협업 가이드

## 브랜치 전략

| 브랜치 | 용도 | 규칙 |
|---|---|---|
| `main` | 발표/제출용 안정 버전 | 직접 push 금지. `develop`에서 PR로만 반영 |
| `develop` | 통합 브랜치 | 직접 push 금지. 작업 브랜치에서 PR로만 반영 |
| `feature/<계층>-<요약>` | 기능 작업 | `develop`에서 분기, `develop`으로 PR |
| `fix/<요약>` | 버그 수정 | `develop`에서 분기, `develop`으로 PR |

계층 이름: `collectors`, `preprocessing`, `database`, `analysis`, `rag`, `dashboard`, `common`, `docs`

역할 예시 (가이드 11-2): 담당 A — `feature/collectors-news`, `feature/analysis-sentiment`, 담당 B — `feature/collectors-dart`, `feature/rag-pipeline`

## 작업 흐름

```bash
git switch develop
git pull origin develop
git switch -c feature/collectors-dart
# ... 작업 & 커밋 ...
pytest                                  # 계층 경계(import) 검사 포함
git fetch origin
git rebase origin/develop      # 충돌은 내 브랜치에서 해결
git push -u origin feature/collectors-dart
# GitHub에서 develop 대상으로 PR 생성 → 리뷰 1명 승인 후 merge
```

- 한 PR은 가능한 한 **한 계층**만 다룬다. 계층 간 인터페이스(스키마) 변경은 별도 PR로 분리한다.
- `src/database/db_manager.py`(스키마)와 `config/` 변경은 두 사람 모두에게 영향을 주므로 상대방 리뷰를 필수로 받는다.
- PR은 **Create a merge commit**으로 합친다 (Squash는 커밋별 작성자 기록이 사라진다).
- merge 후 작업 브랜치는 삭제한다.
- `rebase`와 force push는 **혼자 쓰는 브랜치에서만** 한다. 둘 이상이 push한 브랜치에서는 `git pull`(merge)을 쓴다.
  부득이하게 히스토리를 고칠 때는 `git push --force-with-lease`를 쓰고 팀에 먼저 알린다.
- 주요 단계마다 `develop` → `main` PR로 릴리스하고 태그(`v0.1` 등)를 단다.

## 커밋 메시지

```
<type>(<계층>): <요약>

feat(collectors): DART 공시 목록 수집 추가
fix(rag): 근거 없는 질문에서 거절되지 않던 문제 수정
docs(analysis): 감성 분류 검증 리포트 추가
```

type: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

## 최초 설정

```bash
git clone https://github.com/Hyeonwoo01/accounting_prj.git
cd accounting_prj
python -m venv .venv && source .venv/bin/activate   # Windows: python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env   # Windows: Copy-Item .env.example .env / 본인 키 입력. 절대 커밋 금지
```

## GitHub 저장소 설정 (관리자 1회)

Settings → Branches → Branch protection rules 에서 `main`, `develop` 각각:

- Require a pull request before merging (Required approvals: 1)
- Block force pushes
- Do not allow bypassing the above settings

Settings → General → Default branch 를 `develop`으로 지정하면 PR 기본 대상이 `develop`이 된다.
Settings → Code security → Secret scanning / Push protection 활성화를 권장한다.

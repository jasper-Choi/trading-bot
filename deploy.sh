#!/bin/bash
# deploy.sh — 코드 푸시 후 Oracle VM 배포 + 사이클 성공 자동 확인
# 사용법: bash deploy.sh

KEY="C:/Users/User/Desktop/trading-bot/trading_company_v2/오라클 SSH키/ssh-key-2026-04-21.key"
VM="ubuntu@134.185.118.144"
MAX_WAIT=120   # 최대 120초 대기

echo "=== [1/3] Oracle VM pull & restart ==="
ssh -i "$KEY" "$VM" "
  cd ~/trading-bot && git pull origin main &&
  sudo systemctl restart trading-loop trading-dashboard &&
  echo 'RESTART OK'
"

echo ""
echo "=== [2/3] 사이클 성공 대기 (최대 ${MAX_WAIT}s) ==="
ELAPSED=0
SUCCESS=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  sleep 10
  ELAPSED=$((ELAPSED + 10))
  RESULT=$(ssh -i "$KEY" "$VM" "sudo journalctl -u trading-loop --since '1 minute ago' --no-pager | grep 'stance=' | tail -1" 2>/dev/null)
  if [ -n "$RESULT" ]; then
    echo "사이클 성공 확인: $RESULT"
    SUCCESS=1
    break
  fi
  ERROR=$(ssh -i "$KEY" "$VM" "sudo journalctl -u trading-loop --since '1 minute ago' --no-pager | grep 'cycle failed' | tail -1" 2>/dev/null)
  if [ -n "$ERROR" ]; then
    echo "ERROR: 사이클 실패 감지 — $ERROR"
    echo "즉시 로그 확인: ssh -i '$KEY' $VM 'sudo journalctl -u trading-loop -n 30 --no-pager'"
    exit 1
  fi
  echo "  대기 중... (${ELAPSED}s)"
done

if [ $SUCCESS -eq 0 ]; then
  echo "TIMEOUT: ${MAX_WAIT}s 내에 사이클 확인 안 됨. 로그 수동 확인 필요."
  exit 1
fi

echo ""
echo "=== [3/3] 최근 로그 ==="
ssh -i "$KEY" "$VM" "sudo journalctl -u trading-loop -n 5 --no-pager"
echo ""
echo "배포 완료."

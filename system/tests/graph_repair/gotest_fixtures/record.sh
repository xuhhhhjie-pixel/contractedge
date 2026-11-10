#!/bin/sh
# Record real `go test -json` outputs for the behaviour-check adapter tests.
# Usage: record.sh IMAGE OUTDIR   (runs throwaway containers: no network, read-only root, writable /tmp)
set -eu
IMAGE=$1
OUT=$2
SRC=$(mktemp -d /tmp/gotest-src.XXXXXX)
mkdir -p "$OUT" "$SRC/m/calc" "$SRC/m/broken" "$SRC/m/notests" "$SRC/nomod"
cat > "$SRC/m/go.mod" <<'EOF'
module example.com/m

go 1.21
EOF
cat > "$SRC/m/calc/calc.go" <<'EOF'
package calc

func Add(a, b int) int { return a + b }
EOF
cat > "$SRC/m/calc/calc_test.go" <<'EOF'
package calc

import "testing"

func TestAdd(t *testing.T) {
	if got := Add(1, 2); got != 3 {
		t.Fatalf("Add(1, 2) = %d, want 3", got)
	}
}

func TestWrong(t *testing.T) {
	if got := Add(2, 2); got != 5 {
		t.Errorf("Add(2, 2) = %d, want 5", got)
	}
}

func TestTable(t *testing.T) {
	for _, c := range []struct{ name string; a, b, want int }{{"ok", 1, 1, 2}, {"bad", 1, 1, 3}} {
		t.Run(c.name, func(t *testing.T) {
			if got := Add(c.a, c.b); got != c.want {
				t.Errorf("Add = %d, want %d", got, c.want)
			}
		})
	}
}

func TestSkipped(t *testing.T) { t.Skip("not on this platform") }

func TestPanic(t *testing.T) { var m map[string]int; m["x"] = 1 }
EOF
cat > "$SRC/m/broken/broken.go" <<'EOF'
package broken

func F() int { return undefinedName }
EOF
cat > "$SRC/m/broken/broken_test.go" <<'EOF'
package broken

import "testing"

func TestF(t *testing.T) { _ = F() }
EOF
cat > "$SRC/m/notests/notests.go" <<'EOF'
package notests

func G() {}
EOF
cp "$SRC/m/calc/calc.go" "$SRC/nomod/calc.go"
chmod -R a+rX "$SRC"

run() {
  name=$1; shift
  rc=0
  docker run --rm --network none --read-only --tmpfs /tmp:rw,exec,size=512m --user 1000:1000 \
    -e HOME=/tmp -v "$SRC:/src:ro" "$@" > "$OUT/$name.out" 2>&1 || rc=$?
  echo "$rc" > "$OUT/$name.rc"
  echo "$name rc=$rc"
}
CACHE="-e GOCACHE=/tmp/gocache -e GOPATH=/tmp/gopath -e GOFLAGS=-mod=mod"
# shellcheck disable=SC2086
run pass      $CACHE -w /src/m "$IMAGE" go test -json -count=1 -run '^TestAdd$' ./calc
run fail      $CACHE -w /src/m "$IMAGE" go test -json -count=1 -run '^TestWrong$' ./calc
run subtest   $CACHE -w /src/m "$IMAGE" go test -json -count=1 -run '^TestTable$' ./calc
run skip      $CACHE -w /src/m "$IMAGE" go test -json -count=1 -run '^TestSkipped$' ./calc
run panic     $CACHE -w /src/m "$IMAGE" go test -json -count=1 -run '^TestPanic$' ./calc
run nomatch   $CACHE -w /src/m "$IMAGE" go test -json -count=1 -run '^TestNope$' ./calc
run build     $CACHE -w /src/m "$IMAGE" go test -json -count=1 -run '^TestF$' ./broken
run notests   $CACHE -w /src/m "$IMAGE" go test -json -count=1 -run '^TestG$' ./notests
run nomod     $CACHE -w /src/nomod "$IMAGE" go test -json -count=1 -run '^TestAdd$' .
run rocache   -e HOME=/nonexistent -e GOCACHE=/src/m/.cache -w /src/m "$IMAGE" go test -json -count=1 -run '^TestAdd$' ./calc
run notfound  $CACHE -w /src/m "$IMAGE" /bin/sh -c 'gox test -json ./calc'
docker run --rm "$IMAGE" go version > "$OUT/VERSION"
rm -rf "$SRC"

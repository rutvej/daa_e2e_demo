#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
echo "Triggering Scenario A: Redis OOM via load test..."
$DIR/../load_test.sh

#!/usr/bin/env bash

cp -r /home/user/package /home/user/package_copy
cd /home/user/package_copy
mv setup.py setup.py_
echo "#!/usr/bin/env python" > setup.py
egrep "from\\s+__future__\\s+import" setup.py_ >> setup.py
cat /home/user/sparams.py >> setup.py
egrep -v "from\\s+__future__\\s+import" setup.py_ >> setup.py

TIMEOUT="timeout --kill-after=5 --signal=9 30"

# some files have syntax errors, so it's better to suppress warnings
${TIMEOUT} python3 setup.py install > /dev/null 2>/dev/null

if [[ $? -eq 0 ]]; then
    cat output.json
    exit 0
fi

${TIMEOUT} python setup.py install > /dev/null 2>/dev/null

if [[ $? -eq 0 ]]; then
    cat output.json
    exit 0
fi

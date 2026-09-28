"""执行 init.sql 到目标 MySQL。运行后即可删除本脚本。"""
import pymysql

HOST = "gz-cynosdbmysql-grp-g82l5jpt.sql.tencentcdb.com"
PORT = 25349
USER = "root"
PASSWORD = "T48m.j{]5xJ!Qp"
DB = "sai-flow"

sql = """
CREATE DATABASE IF NOT EXISTS `sai-flow`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `sai-flow`.`users` (
    `id`            INT          NOT NULL AUTO_INCREMENT,
    `username`      VARCHAR(80)  NOT NULL,
    `email`         VARCHAR(120) NOT NULL,
    `password_hash` VARCHAR(255) NOT NULL,
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_users_username` (`username`),
    UNIQUE KEY `uq_users_email`    (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

# 1) 不指定库连接，执行建库语句
conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, charset="utf8mb4")
try:
    with conn.cursor() as cur:
        # 拆分语句逐条执行
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            # 建表语句带库名前缀 `sai-flow`.`users`，可直接执行
            cur.execute(stmt)
    conn.commit()
    print("建库 / 建表执行完成")
finally:
    conn.close()

# 2) 切到目标库验证表是否存在
conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=DB, charset="utf8mb4")
try:
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES")
        tables = [r[0] for r in cur.fetchall()]
        print(f"库 `{DB}` 现有表: {tables}")
        cur.execute("DESCRIBE `users`")
        for row in cur.fetchall():
            print(" ", row)
finally:
    conn.close()

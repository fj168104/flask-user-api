"""按 PRD §13 重建 sai-flow 库结构（P0）。

警告：会 DROP 旧的 demo users 表（用户名/密码演示数据，无生产价值）。
表结构与本仓库 models.py 一一对应；应用启动时 db.create_all() 亦可
自动补齐增量，本脚本用于一次性干净重建。
"""
import pymysql

HOST = "gz-cynosdbmysql-grp-g82l5jpt.sql.tencentcdb.com"
PORT = 25349
USER = "root"
PASSWORD = "T48m.j{]5xJ!Qp"
DB = "sai-flow"

STATEMENTS = [
    "DROP DATABASE IF EXISTS `sai-flow`",
    "CREATE DATABASE `sai-flow` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci",
    """
    CREATE TABLE `sai-flow`.`users` (
        `id`              BIGINT       NOT NULL AUTO_INCREMENT,
        `line_provider`   VARCHAR(32)  NOT NULL DEFAULT 'line',
        `line_sub`        VARCHAR(128) NOT NULL,
        `display_name`    VARCHAR(128) NOT NULL DEFAULT '',
        `avatar_url`      VARCHAR(512) NOT NULL DEFAULT '',
        `language`        VARCHAR(8)   NOT NULL DEFAULT 'th',
        `status`          VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE',
        `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `updated_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_users_line_identity` (`line_provider`, `line_sub`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`sessions` (
        `id`                   BIGINT       NOT NULL AUTO_INCREMENT,
        `token_hash`           VARCHAR(64)  NOT NULL,
        `user_id`              BIGINT       NOT NULL,
        `csrf_hash`            VARCHAR(64)  NOT NULL,
        `issued_at`            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `absolute_expires_at`  DATETIME     NOT NULL,
        `idle_expires_at`      DATETIME     NOT NULL,
        `revoked_at`           DATETIME     NULL,
        `created_ip`           VARCHAR(64)  NOT NULL DEFAULT '',
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_sessions_token_hash` (`token_hash`),
        KEY `ix_sessions_user_id` (`user_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`enterprises` (
        `id`              BIGINT       NOT NULL AUTO_INCREMENT,
        `code`            VARCHAR(32)  NOT NULL,
        `country`         VARCHAR(8)   NOT NULL DEFAULT 'TH',
        `legal_name`      VARCHAR(255) NOT NULL DEFAULT '',
        `registration_no` VARCHAR(64)  NOT NULL DEFAULT '',
        `status`          VARCHAR(32)  NOT NULL DEFAULT 'PENDING_VERIFICATION',
        `external_ref`    VARCHAR(64)  NOT NULL DEFAULT '',
        `policy_version`  INT          NOT NULL DEFAULT 1,
        `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `updated_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_enterprises_code` (`code`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`memberships` (
        `id`            BIGINT      NOT NULL AUTO_INCREMENT,
        `enterprise_id` BIGINT      NOT NULL,
        `user_id`       BIGINT      NOT NULL,
        `role`          VARCHAR(16) NOT NULL DEFAULT 'MEMBER',
        `status`        VARCHAR(16) NOT NULL DEFAULT 'PENDING',
        `approved_via`  VARCHAR(32) NOT NULL DEFAULT '',
        `decided_at`    DATETIME    NULL,
        `revoked_at`    DATETIME    NULL,
        `created_at`    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `updated_at`    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_membership_enterprise_user` (`enterprise_id`, `user_id`),
        KEY `ix_memberships_user_id` (`user_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`applications` (
        `id`                   BIGINT      NOT NULL AUTO_INCREMENT,
        `type`                 VARCHAR(32) NOT NULL,
        `applicant_user_id`    BIGINT      NOT NULL,
        `target_enterprise_id` BIGINT      NULL,
        `form`                 JSON        NOT NULL,
        `status`               VARCHAR(24) NOT NULL DEFAULT 'DRAFT',
        `version`              INT         NOT NULL DEFAULT 1,
        `review_note`          VARCHAR(512) NOT NULL DEFAULT '',
        `submitted_at`         DATETIME    NULL,
        `created_at`           DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `updated_at`           DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        KEY `ix_applications_applicant` (`applicant_user_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`invitations` (
        `id`                 BIGINT      NOT NULL AUTO_INCREMENT,
        `token_hash`         VARCHAR(64) NOT NULL,
        `enterprise_id`      BIGINT      NOT NULL,
        `created_by_user_id` BIGINT      NOT NULL,
        `max_uses`           INT         NOT NULL DEFAULT 1,
        `use_count`          INT         NOT NULL DEFAULT 0,
        `expires_at`         DATETIME    NOT NULL,
        `status`             VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_invitations_token_hash` (`token_hash`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`files` (
        `id`                 VARCHAR(36)  NOT NULL,
        `workspace_type`     VARCHAR(16)  NOT NULL DEFAULT 'PERSONAL',
        `enterprise_id`      BIGINT       NULL,
        `created_by_user_id` BIGINT       NOT NULL,
        `uploader_user_id`   BIGINT       NOT NULL,
        `purpose`            VARCHAR(32)  NOT NULL DEFAULT 'OCR_TASK',
        `original_name`      VARCHAR(255) NOT NULL DEFAULT '',
        `storage_path`       VARCHAR(512) NOT NULL DEFAULT '',
        `mime_type`          VARCHAR(128) NOT NULL DEFAULT '',
        `size_bytes`         BIGINT       NOT NULL DEFAULT 0,
        `page_count`         INT          NOT NULL DEFAULT 0,
        `sha256`             VARCHAR(64)  NOT NULL DEFAULT '',
        `status`             VARCHAR(16)  NOT NULL DEFAULT 'UPLOADING',
        `reject_reason`      VARCHAR(255) NOT NULL DEFAULT '',
        `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `updated_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        KEY `ix_files_created_by` (`created_by_user_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`ocr_jobs` (
        `id`                 VARCHAR(36)  NOT NULL,
        `workspace_type`     VARCHAR(16)  NOT NULL DEFAULT 'PERSONAL',
        `enterprise_id`      BIGINT       NULL,
        `created_by_user_id` BIGINT       NOT NULL,
        `file_id`            VARCHAR(36)  NOT NULL,
        `params`             JSON         NOT NULL,
        `capability`         JSON         NOT NULL,
        `status`             VARCHAR(24)  NOT NULL DEFAULT 'QUEUED',
        `idempotency_key`    VARCHAR(128) NULL,
        `parent_job_id`      VARCHAR(36)  NULL,
        `progress`           INT          NOT NULL DEFAULT 0,
        `error_code`         VARCHAR(64)  NOT NULL DEFAULT '',
        `error_message`      VARCHAR(512) NOT NULL DEFAULT '',
        `vendor_request_id`  VARCHAR(64)  NOT NULL DEFAULT '',
        `page_count`         INT          NOT NULL DEFAULT 0,
        `table_count`        INT          NOT NULL DEFAULT 0,
        `field_count`        INT          NOT NULL DEFAULT 0,
        `duration_ms`        BIGINT       NOT NULL DEFAULT 0,
        `missing_pages`      JSON         NULL,
        `review_status`      VARCHAR(16)  NOT NULL DEFAULT 'UNREVIEWED',
        `started_at`         DATETIME     NULL,
        `finished_at`        DATETIME     NULL,
        `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `updated_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_ocr_jobs_idempotency` (`idempotency_key`),
        KEY `ix_ocr_jobs_status` (`status`),
        KEY `ix_ocr_jobs_created_by` (`created_by_user_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`result_versions` (
        `id`                  VARCHAR(36) NOT NULL,
        `job_id`              VARCHAR(36) NOT NULL,
        `version`             INT         NOT NULL DEFAULT 1,
        `is_current`          TINYINT(1)  NOT NULL DEFAULT 1,
        `created_by_user_id`  BIGINT      NULL,
        `created_at`          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_result_version_job_version` (`job_id`, `version`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`page_results` (
        `id`                 VARCHAR(36) NOT NULL,
        `result_version_id`  VARCHAR(36) NOT NULL,
        `page_number`        INT         NOT NULL,
        `text`               TEXT        NOT NULL,
        `tables`             JSON        NOT NULL,
        `fields`             JSON        NOT NULL,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_page_result_version_page` (`result_version_id`, `page_number`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`exports` (
        `id`                 VARCHAR(36)  NOT NULL,
        `job_id`             VARCHAR(36)  NOT NULL,
        `result_version_id`  VARCHAR(36)  NOT NULL,
        `format`             VARCHAR(8)   NOT NULL,
        `status`             VARCHAR(16)  NOT NULL DEFAULT 'GENERATING',
        `storage_path`       VARCHAR(512) NOT NULL DEFAULT '',
        `expires_at`         DATETIME     NULL,
        `created_by_user_id` BIGINT       NOT NULL,
        `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `updated_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        KEY `ix_exports_job_id` (`job_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`notifications` (
        `id`         BIGINT       NOT NULL AUTO_INCREMENT,
        `user_id`    BIGINT       NOT NULL,
        `type`       VARCHAR(32)  NOT NULL DEFAULT 'INFO',
        `title`      VARCHAR(128) NOT NULL DEFAULT '',
        `body`       VARCHAR(512) NOT NULL DEFAULT '',
        `read_at`    DATETIME     NULL,
        `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        KEY `ix_notifications_user` (`user_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`audit_events` (
        `id`          BIGINT      NOT NULL AUTO_INCREMENT,
        `user_id`     BIGINT      NOT NULL,
        `action`      VARCHAR(64) NOT NULL,
        `object_type` VARCHAR(32) NOT NULL DEFAULT '',
        `object_id`   VARCHAR(64) NOT NULL DEFAULT '',
        `detail`      JSON        NOT NULL,
        `created_at`  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        KEY `ix_audit_user` (`user_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`data_requests` (
        `id`           BIGINT      NOT NULL AUTO_INCREMENT,
        `user_id`      BIGINT      NOT NULL,
        `kind`         VARCHAR(16) NOT NULL,
        `status`       VARCHAR(16) NOT NULL DEFAULT 'PENDING',
        `note`         VARCHAR(255) NOT NULL DEFAULT '',
        `created_at`   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        `processed_at` DATETIME    NULL,
        PRIMARY KEY (`id`),
        KEY `ix_data_requests_user` (`user_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE `sai-flow`.`review_sync_cursors` (
        `id`         BIGINT       NOT NULL AUTO_INCREMENT,
        `source`     VARCHAR(32)  NOT NULL,
        `cursor`     VARCHAR(128) NOT NULL DEFAULT '',
        `updated_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (`id`),
        UNIQUE KEY `uq_review_sync_source` (`source`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

if __name__ == "__main__":
    conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            for stmt in STATEMENTS:
                cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()

    conn = pymysql.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=DB, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            print(f"库 `{DB}` 现有表: {[r[0] for r in cur.fetchall()]}")
    finally:
        conn.close()

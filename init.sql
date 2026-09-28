-- ============================================================
-- Flask 用户服务建表脚本
-- 目标库: sai-flow  引擎: InnoDB  字符集: utf8mb4
-- ============================================================

-- 若库不存在则创建（库名含连字符，需用反引号）
CREATE DATABASE IF NOT EXISTS `sai-flow`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `sai-flow`;

-- 用户表
CREATE TABLE IF NOT EXISTS `users` (
    `id`            INT          NOT NULL AUTO_INCREMENT,
    `username`      VARCHAR(80)  NOT NULL,
    `email`         VARCHAR(120) NOT NULL,
    `password_hash` VARCHAR(255) NOT NULL,
    `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_users_username` (`username`),
    UNIQUE KEY `uq_users_email`    (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

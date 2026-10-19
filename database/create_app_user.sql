-- =====================================================================================
-- Optional: a dedicated MySQL login for the app, instead of root.
--
-- 1. Replace <choose a password> below.
-- 2. Run as an administrator, after schema.sql:
--        mysql -u root -p < database/create_app_user.sql
-- 3. In .env set  MYSQL_USER=medishield_app  and  MYSQL_PASSWORD=<the same password>
--
-- The app only needs the rights granted here on its own database. The backend also runs
-- CREATE TABLE / ALTER TABLE at start-up (no-ops once schema.sql has run), so the schema
-- rights are kept.
-- =====================================================================================

CREATE USER IF NOT EXISTS 'medishield_app'@'localhost' IDENTIFIED BY '<choose a password>';
CREATE USER IF NOT EXISTS 'medishield_app'@'127.0.0.1' IDENTIFIED BY '<choose a password>';

GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX
    ON `medishield`.* TO 'medishield_app'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX
    ON `medishield`.* TO 'medishield_app'@'127.0.0.1';

FLUSH PRIVILEGES;

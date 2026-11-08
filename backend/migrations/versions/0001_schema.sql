CREATE EXTENSION IF NOT EXISTS vector;
-- statement-break
CREATE EXTENSION IF NOT EXISTS btree_gist;
-- statement-break

CREATE TABLE auth_attempts (
	bucket VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id)
)

;
-- statement-break
CREATE INDEX ix_auth_attempts_bucket ON auth_attempts (bucket);
-- statement-break

CREATE TABLE departments (
	name VARCHAR(160) NOT NULL, 
	code VARCHAR(24) NOT NULL, 
	parent_id VARCHAR(36), 
	registry_complete BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code), 
	FOREIGN KEY(parent_id) REFERENCES departments (id)
)

;
-- statement-break

CREATE TABLE audit_events (
	kind VARCHAR(64) NOT NULL, 
	category VARCHAR(20) NOT NULL, 
	actor_id VARCHAR(36), 
	department_id VARCHAR(36), 
	object_id VARCHAR(36), 
	occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	correlation_id VARCHAR(36) NOT NULL, 
	payload JSONB NOT NULL, 
	signature VARCHAR(64) NOT NULL, 
	sequence INTEGER NOT NULL, 
	previous_signature VARCHAR(64) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	UNIQUE (sequence)
)

;
-- statement-break

CREATE TABLE document_versions (
	department_id VARCHAR(36) NOT NULL, 
	title VARCHAR(240) NOT NULL, 
	revision VARCHAR(30) NOT NULL, 
	effective_from DATE NOT NULL, 
	registered_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	synthetic BOOLEAN NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
)

;
-- statement-break
CREATE INDEX ix_document_versions_department_id ON document_versions (department_id);
-- statement-break

CREATE TABLE employees (
	department_id VARCHAR(36) NOT NULL, 
	display_name VARCHAR(160) NOT NULL, 
	personnel_number VARCHAR(30) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	UNIQUE (personnel_number)
)

;
-- statement-break
CREATE INDEX ix_employees_department_id ON employees (department_id);
-- statement-break

CREATE TABLE positions (
	department_id VARCHAR(36) NOT NULL, 
	title VARCHAR(180) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
)

;
-- statement-break
CREATE INDEX ix_positions_department_id ON positions (department_id);
-- statement-break

CREATE TABLE source_anchors (
	document_version_id VARCHAR(36) NOT NULL, 
	locator VARCHAR(120) NOT NULL, 
	quote TEXT NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(document_version_id) REFERENCES document_versions (id)
)

;
-- statement-break

CREATE TABLE staffing_seats (
	position_id VARCHAR(36) NOT NULL, 
	code VARCHAR(30) NOT NULL, 
	version INTEGER NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(position_id) REFERENCES positions (id), 
	UNIQUE (code)
)

;
-- statement-break
CREATE INDEX ix_staffing_seats_position_id ON staffing_seats (position_id);
-- statement-break

CREATE TABLE user_accounts (
	username VARCHAR(160) NOT NULL, 
	display_name VARCHAR(160) NOT NULL, 
	employee_id VARCHAR(36), 
	password_hash TEXT NOT NULL, 
	active BOOLEAN NOT NULL, 
	mfa_secret TEXT, 
	pending_mfa_secret TEXT, 
	last_totp_step INTEGER NOT NULL, 
	recovery_hashes JSONB NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username), 
	FOREIGN KEY(employee_id) REFERENCES employees (id)
)

;
-- statement-break

CREATE TABLE appointments (
	seat_id VARCHAR(36) NOT NULL, 
	employee_id VARCHAR(36) NOT NULL, 
	start_date DATE NOT NULL, 
	end_date DATE, 
	kind VARCHAR(20) NOT NULL, 
	source_anchor_id VARCHAR(36) NOT NULL, 
	registered_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	registered_by VARCHAR(36) NOT NULL, 
	version INTEGER NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (end_date IS NULL OR end_date > start_date), 
	CHECK (kind IN ('permanent', 'temporary')), 
	CONSTRAINT no_overlapping_appointments EXCLUDE USING gist (seat_id WITH =, daterange(start_date, end_date, '[)') WITH &&), 
	FOREIGN KEY(seat_id) REFERENCES staffing_seats (id), 
	FOREIGN KEY(employee_id) REFERENCES employees (id), 
	FOREIGN KEY(source_anchor_id) REFERENCES source_anchors (id), 
	FOREIGN KEY(registered_by) REFERENCES user_accounts (id)
)

;
-- statement-break
CREATE INDEX ix_appointments_seat_id ON appointments (seat_id);
-- statement-break

CREATE TABLE browser_sessions (
	token_hash VARCHAR(64) NOT NULL, 
	csrf_token VARCHAR(64) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	mfa_verified BOOLEAN NOT NULL, 
	reauthenticated_at TIMESTAMP WITH TIME ZONE, 
	revoked BOOLEAN NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (token_hash), 
	FOREIGN KEY(user_id) REFERENCES user_accounts (id)
)

;
-- statement-break
CREATE INDEX ix_browser_sessions_user_id ON browser_sessions (user_id);
-- statement-break

CREATE TABLE documented_functions (
	department_id VARCHAR(36) NOT NULL, 
	code VARCHAR(24) NOT NULL, 
	title VARCHAR(240) NOT NULL, 
	source_anchor_id VARCHAR(36) NOT NULL, 
	valid_from DATE NOT NULL, 
	abolished_on DATE, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	UNIQUE (code), 
	FOREIGN KEY(source_anchor_id) REFERENCES source_anchors (id)
)

;
-- statement-break
CREATE INDEX ix_documented_functions_department_id ON documented_functions (department_id);
-- statement-break

CREATE TABLE recovery_tokens (
	user_id VARCHAR(36) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	purpose VARCHAR(20) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES user_accounts (id), 
	UNIQUE (token_hash)
)

;
-- statement-break

CREATE TABLE role_assignments (
	user_id VARCHAR(36) NOT NULL, 
	role VARCHAR(32) NOT NULL, 
	department_id VARCHAR(36), 
	valid_from TIMESTAMP WITH TIME ZONE NOT NULL, 
	valid_until TIMESTAMP WITH TIME ZONE, 
	basis VARCHAR(240) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (role IN ('auditor', 'hr', 'department', 'access_admin', 'reviewer', 'manager')), 
	FOREIGN KEY(user_id) REFERENCES user_accounts (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
)

;
-- statement-break
CREATE INDEX ix_role_assignments_user_id ON role_assignments (user_id);
-- statement-break

CREATE TABLE function_assignments (
	function_id VARCHAR(36) NOT NULL, 
	position_id VARCHAR(36), 
	department_id VARCHAR(36) NOT NULL, 
	source_anchor_id VARCHAR(36) NOT NULL, 
	valid_from DATE NOT NULL, 
	valid_until DATE, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(function_id) REFERENCES documented_functions (id), 
	FOREIGN KEY(position_id) REFERENCES positions (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	FOREIGN KEY(source_anchor_id) REFERENCES source_anchors (id)
)

;
-- statement-break
CREATE INDEX ix_function_assignments_function_id ON function_assignments (function_id);
-- statement-break

CREATE TABLE personnel_events (
	department_id VARCHAR(36) NOT NULL, 
	appointment_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	effective_date DATE NOT NULL, 
	registered_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	actor_id VARCHAR(36) NOT NULL, 
	source_anchor_id VARCHAR(36) NOT NULL, 
	corrects_event_id VARCHAR(36), 
	idempotency_key VARCHAR(80) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	before_state JSONB NOT NULL, 
	after_state JSONB NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	FOREIGN KEY(appointment_id) REFERENCES appointments (id), 
	FOREIGN KEY(actor_id) REFERENCES user_accounts (id), 
	FOREIGN KEY(source_anchor_id) REFERENCES source_anchors (id), 
	FOREIGN KEY(corrects_event_id) REFERENCES personnel_events (id), 
	UNIQUE (idempotency_key)
)

;
-- statement-break
CREATE INDEX ix_personnel_events_department_id ON personnel_events (department_id);
-- statement-break

CREATE TABLE notifications (
	user_id VARCHAR(36) NOT NULL, 
	department_id VARCHAR(36), 
	event_id VARCHAR(36), 
	kind VARCHAR(40) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	read_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT notification_once UNIQUE (user_id, event_id), 
	FOREIGN KEY(user_id) REFERENCES user_accounts (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	FOREIGN KEY(event_id) REFERENCES personnel_events (id)
)

;
-- statement-break
CREATE INDEX ix_notifications_user_id ON notifications (user_id);
-- statement-break

CREATE TABLE outbox_records (
	event_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	processed_at TIMESTAMP WITH TIME ZONE, 
	attempts INTEGER NOT NULL, 
	error_code VARCHAR(64), 
	due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (event_id), 
	FOREIGN KEY(event_id) REFERENCES personnel_events (id)
)

;
-- statement-break
GRANT USAGE ON SCHEMA public TO orgproekt_app;
-- statement-break
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO orgproekt_app;
-- statement-break
CREATE FUNCTION reject_immutable_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable_record'; END; $$;
-- statement-break
REVOKE UPDATE, DELETE ON audit_events FROM orgproekt_app;
-- statement-break
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_change();
-- statement-break
REVOKE UPDATE, DELETE ON personnel_events FROM orgproekt_app;
-- statement-break
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON personnel_events FOR EACH ROW EXECUTE FUNCTION reject_immutable_change();
-- statement-break
REVOKE UPDATE, DELETE ON document_versions FROM orgproekt_app;
-- statement-break
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON document_versions FOR EACH ROW EXECUTE FUNCTION reject_immutable_change();
-- statement-break
REVOKE UPDATE, DELETE ON source_anchors FROM orgproekt_app;
-- statement-break
CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON source_anchors FOR EACH ROW EXECUTE FUNCTION reject_immutable_change();
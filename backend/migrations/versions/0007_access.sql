
CREATE TABLE account_controls (
	user_id VARCHAR(36) NOT NULL, 
	version INTEGER NOT NULL, 
	PRIMARY KEY (user_id), 
	FOREIGN KEY(user_id) REFERENCES user_accounts (id)
)

;
-- statement-break

CREATE TABLE account_invitations (
	username VARCHAR(80) NOT NULL, 
	display_name VARCHAR(160) NOT NULL, 
	basis VARCHAR(240) NOT NULL, 
	creator_id VARCHAR(36) NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	delivery_secret TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	accepted_at TIMESTAMP WITH TIME ZONE, 
	cancelled_at TIMESTAMP WITH TIME ZONE, 
	user_id VARCHAR(36), 
	version INTEGER NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(creator_id) REFERENCES user_accounts (id), 
	UNIQUE (token_hash), 
	FOREIGN KEY(user_id) REFERENCES user_accounts (id)
)

;
-- statement-break

CREATE TABLE access_requests (
	user_id VARCHAR(36) NOT NULL, 
	creator_id VARCHAR(36) NOT NULL, 
	role VARCHAR(32) NOT NULL, 
	department_id VARCHAR(36), 
	valid_from TIMESTAMP WITH TIME ZONE NOT NULL, 
	valid_until TIMESTAMP WITH TIME ZONE NOT NULL, 
	basis VARCHAR(240) NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	decider_id VARCHAR(36), 
	decision_note VARCHAR(1000) NOT NULL, 
	assignment_id VARCHAR(36), 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES user_accounts (id), 
	FOREIGN KEY(creator_id) REFERENCES user_accounts (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	FOREIGN KEY(decider_id) REFERENCES user_accounts (id), 
	FOREIGN KEY(assignment_id) REFERENCES role_assignments (id)
)

;
-- statement-break

CREATE TABLE grant_revocations (
	assignment_id VARCHAR(36) NOT NULL, 
	actor_id VARCHAR(36) NOT NULL, 
	basis VARCHAR(240) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (assignment_id), 
	FOREIGN KEY(assignment_id) REFERENCES role_assignments (id), 
	FOREIGN KEY(actor_id) REFERENCES user_accounts (id)
)

;
-- statement-break

CREATE TABLE access_receipts (
	actor_id VARCHAR(36) NOT NULL, 
	idempotency_key VARCHAR(80) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	object_kind VARCHAR(24) NOT NULL, 
	object_id VARCHAR(36) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(actor_id) REFERENCES user_accounts (id), 
	UNIQUE (idempotency_key)
)

;
-- statement-break
CREATE INDEX ix_account_invitations_username ON account_invitations (username);
-- statement-break
CREATE INDEX ix_access_requests_state ON access_requests (state);
-- statement-break
CREATE INDEX ix_access_requests_user_id ON access_requests (user_id);

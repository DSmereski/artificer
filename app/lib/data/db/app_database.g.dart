// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'app_database.dart';

// ignore_for_file: type=lint
class $TaskRowsTable extends TaskRows with TableInfo<$TaskRowsTable, TaskRow> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $TaskRowsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _slugMeta = const VerificationMeta('slug');
  @override
  late final GeneratedColumn<String> slug = GeneratedColumn<String>(
    'slug',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _titleMeta = const VerificationMeta('title');
  @override
  late final GeneratedColumn<String> title = GeneratedColumn<String>(
    'title',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _statusMeta = const VerificationMeta('status');
  @override
  late final GeneratedColumn<String> status = GeneratedColumn<String>(
    'status',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _projectSlugMeta = const VerificationMeta(
    'projectSlug',
  );
  @override
  late final GeneratedColumn<String> projectSlug = GeneratedColumn<String>(
    'project_slug',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _assigneeMeta = const VerificationMeta(
    'assignee',
  );
  @override
  late final GeneratedColumn<String> assignee = GeneratedColumn<String>(
    'assignee',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _priorityMeta = const VerificationMeta(
    'priority',
  );
  @override
  late final GeneratedColumn<String> priority = GeneratedColumn<String>(
    'priority',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _payloadJsonMeta = const VerificationMeta(
    'payloadJson',
  );
  @override
  late final GeneratedColumn<String> payloadJson = GeneratedColumn<String>(
    'payload_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<String> updatedAt = GeneratedColumn<String>(
    'updated_at',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _dirtyMeta = const VerificationMeta('dirty');
  @override
  late final GeneratedColumn<bool> dirty = GeneratedColumn<bool>(
    'dirty',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("dirty" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _pendingDeleteMeta = const VerificationMeta(
    'pendingDelete',
  );
  @override
  late final GeneratedColumn<bool> pendingDelete = GeneratedColumn<bool>(
    'pending_delete',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("pending_delete" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  @override
  List<GeneratedColumn> get $columns => [
    slug,
    title,
    status,
    projectSlug,
    assignee,
    priority,
    payloadJson,
    updatedAt,
    dirty,
    pendingDelete,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'task_rows';
  @override
  VerificationContext validateIntegrity(
    Insertable<TaskRow> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('slug')) {
      context.handle(
        _slugMeta,
        slug.isAcceptableOrUnknown(data['slug']!, _slugMeta),
      );
    } else if (isInserting) {
      context.missing(_slugMeta);
    }
    if (data.containsKey('title')) {
      context.handle(
        _titleMeta,
        title.isAcceptableOrUnknown(data['title']!, _titleMeta),
      );
    } else if (isInserting) {
      context.missing(_titleMeta);
    }
    if (data.containsKey('status')) {
      context.handle(
        _statusMeta,
        status.isAcceptableOrUnknown(data['status']!, _statusMeta),
      );
    } else if (isInserting) {
      context.missing(_statusMeta);
    }
    if (data.containsKey('project_slug')) {
      context.handle(
        _projectSlugMeta,
        projectSlug.isAcceptableOrUnknown(
          data['project_slug']!,
          _projectSlugMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_projectSlugMeta);
    }
    if (data.containsKey('assignee')) {
      context.handle(
        _assigneeMeta,
        assignee.isAcceptableOrUnknown(data['assignee']!, _assigneeMeta),
      );
    } else if (isInserting) {
      context.missing(_assigneeMeta);
    }
    if (data.containsKey('priority')) {
      context.handle(
        _priorityMeta,
        priority.isAcceptableOrUnknown(data['priority']!, _priorityMeta),
      );
    } else if (isInserting) {
      context.missing(_priorityMeta);
    }
    if (data.containsKey('payload_json')) {
      context.handle(
        _payloadJsonMeta,
        payloadJson.isAcceptableOrUnknown(
          data['payload_json']!,
          _payloadJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_payloadJsonMeta);
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_updatedAtMeta);
    }
    if (data.containsKey('dirty')) {
      context.handle(
        _dirtyMeta,
        dirty.isAcceptableOrUnknown(data['dirty']!, _dirtyMeta),
      );
    }
    if (data.containsKey('pending_delete')) {
      context.handle(
        _pendingDeleteMeta,
        pendingDelete.isAcceptableOrUnknown(
          data['pending_delete']!,
          _pendingDeleteMeta,
        ),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {slug};
  @override
  TaskRow map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return TaskRow(
      slug: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}slug'],
      )!,
      title: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}title'],
      )!,
      status: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}status'],
      )!,
      projectSlug: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}project_slug'],
      )!,
      assignee: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}assignee'],
      )!,
      priority: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}priority'],
      )!,
      payloadJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}payload_json'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}updated_at'],
      )!,
      dirty: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}dirty'],
      )!,
      pendingDelete: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}pending_delete'],
      )!,
    );
  }

  @override
  $TaskRowsTable createAlias(String alias) {
    return $TaskRowsTable(attachedDatabase, alias);
  }
}

class TaskRow extends DataClass implements Insertable<TaskRow> {
  final String slug;
  final String title;
  final String status;
  final String projectSlug;
  final String assignee;
  final String priority;
  final String payloadJson;
  final String updatedAt;
  final bool dirty;
  final bool pendingDelete;
  const TaskRow({
    required this.slug,
    required this.title,
    required this.status,
    required this.projectSlug,
    required this.assignee,
    required this.priority,
    required this.payloadJson,
    required this.updatedAt,
    required this.dirty,
    required this.pendingDelete,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['slug'] = Variable<String>(slug);
    map['title'] = Variable<String>(title);
    map['status'] = Variable<String>(status);
    map['project_slug'] = Variable<String>(projectSlug);
    map['assignee'] = Variable<String>(assignee);
    map['priority'] = Variable<String>(priority);
    map['payload_json'] = Variable<String>(payloadJson);
    map['updated_at'] = Variable<String>(updatedAt);
    map['dirty'] = Variable<bool>(dirty);
    map['pending_delete'] = Variable<bool>(pendingDelete);
    return map;
  }

  TaskRowsCompanion toCompanion(bool nullToAbsent) {
    return TaskRowsCompanion(
      slug: Value(slug),
      title: Value(title),
      status: Value(status),
      projectSlug: Value(projectSlug),
      assignee: Value(assignee),
      priority: Value(priority),
      payloadJson: Value(payloadJson),
      updatedAt: Value(updatedAt),
      dirty: Value(dirty),
      pendingDelete: Value(pendingDelete),
    );
  }

  factory TaskRow.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return TaskRow(
      slug: serializer.fromJson<String>(json['slug']),
      title: serializer.fromJson<String>(json['title']),
      status: serializer.fromJson<String>(json['status']),
      projectSlug: serializer.fromJson<String>(json['projectSlug']),
      assignee: serializer.fromJson<String>(json['assignee']),
      priority: serializer.fromJson<String>(json['priority']),
      payloadJson: serializer.fromJson<String>(json['payloadJson']),
      updatedAt: serializer.fromJson<String>(json['updatedAt']),
      dirty: serializer.fromJson<bool>(json['dirty']),
      pendingDelete: serializer.fromJson<bool>(json['pendingDelete']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'slug': serializer.toJson<String>(slug),
      'title': serializer.toJson<String>(title),
      'status': serializer.toJson<String>(status),
      'projectSlug': serializer.toJson<String>(projectSlug),
      'assignee': serializer.toJson<String>(assignee),
      'priority': serializer.toJson<String>(priority),
      'payloadJson': serializer.toJson<String>(payloadJson),
      'updatedAt': serializer.toJson<String>(updatedAt),
      'dirty': serializer.toJson<bool>(dirty),
      'pendingDelete': serializer.toJson<bool>(pendingDelete),
    };
  }

  TaskRow copyWith({
    String? slug,
    String? title,
    String? status,
    String? projectSlug,
    String? assignee,
    String? priority,
    String? payloadJson,
    String? updatedAt,
    bool? dirty,
    bool? pendingDelete,
  }) => TaskRow(
    slug: slug ?? this.slug,
    title: title ?? this.title,
    status: status ?? this.status,
    projectSlug: projectSlug ?? this.projectSlug,
    assignee: assignee ?? this.assignee,
    priority: priority ?? this.priority,
    payloadJson: payloadJson ?? this.payloadJson,
    updatedAt: updatedAt ?? this.updatedAt,
    dirty: dirty ?? this.dirty,
    pendingDelete: pendingDelete ?? this.pendingDelete,
  );
  TaskRow copyWithCompanion(TaskRowsCompanion data) {
    return TaskRow(
      slug: data.slug.present ? data.slug.value : this.slug,
      title: data.title.present ? data.title.value : this.title,
      status: data.status.present ? data.status.value : this.status,
      projectSlug: data.projectSlug.present
          ? data.projectSlug.value
          : this.projectSlug,
      assignee: data.assignee.present ? data.assignee.value : this.assignee,
      priority: data.priority.present ? data.priority.value : this.priority,
      payloadJson: data.payloadJson.present
          ? data.payloadJson.value
          : this.payloadJson,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
      dirty: data.dirty.present ? data.dirty.value : this.dirty,
      pendingDelete: data.pendingDelete.present
          ? data.pendingDelete.value
          : this.pendingDelete,
    );
  }

  @override
  String toString() {
    return (StringBuffer('TaskRow(')
          ..write('slug: $slug, ')
          ..write('title: $title, ')
          ..write('status: $status, ')
          ..write('projectSlug: $projectSlug, ')
          ..write('assignee: $assignee, ')
          ..write('priority: $priority, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('dirty: $dirty, ')
          ..write('pendingDelete: $pendingDelete')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    slug,
    title,
    status,
    projectSlug,
    assignee,
    priority,
    payloadJson,
    updatedAt,
    dirty,
    pendingDelete,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is TaskRow &&
          other.slug == this.slug &&
          other.title == this.title &&
          other.status == this.status &&
          other.projectSlug == this.projectSlug &&
          other.assignee == this.assignee &&
          other.priority == this.priority &&
          other.payloadJson == this.payloadJson &&
          other.updatedAt == this.updatedAt &&
          other.dirty == this.dirty &&
          other.pendingDelete == this.pendingDelete);
}

class TaskRowsCompanion extends UpdateCompanion<TaskRow> {
  final Value<String> slug;
  final Value<String> title;
  final Value<String> status;
  final Value<String> projectSlug;
  final Value<String> assignee;
  final Value<String> priority;
  final Value<String> payloadJson;
  final Value<String> updatedAt;
  final Value<bool> dirty;
  final Value<bool> pendingDelete;
  final Value<int> rowid;
  const TaskRowsCompanion({
    this.slug = const Value.absent(),
    this.title = const Value.absent(),
    this.status = const Value.absent(),
    this.projectSlug = const Value.absent(),
    this.assignee = const Value.absent(),
    this.priority = const Value.absent(),
    this.payloadJson = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.dirty = const Value.absent(),
    this.pendingDelete = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  TaskRowsCompanion.insert({
    required String slug,
    required String title,
    required String status,
    required String projectSlug,
    required String assignee,
    required String priority,
    required String payloadJson,
    required String updatedAt,
    this.dirty = const Value.absent(),
    this.pendingDelete = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : slug = Value(slug),
       title = Value(title),
       status = Value(status),
       projectSlug = Value(projectSlug),
       assignee = Value(assignee),
       priority = Value(priority),
       payloadJson = Value(payloadJson),
       updatedAt = Value(updatedAt);
  static Insertable<TaskRow> custom({
    Expression<String>? slug,
    Expression<String>? title,
    Expression<String>? status,
    Expression<String>? projectSlug,
    Expression<String>? assignee,
    Expression<String>? priority,
    Expression<String>? payloadJson,
    Expression<String>? updatedAt,
    Expression<bool>? dirty,
    Expression<bool>? pendingDelete,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (slug != null) 'slug': slug,
      if (title != null) 'title': title,
      if (status != null) 'status': status,
      if (projectSlug != null) 'project_slug': projectSlug,
      if (assignee != null) 'assignee': assignee,
      if (priority != null) 'priority': priority,
      if (payloadJson != null) 'payload_json': payloadJson,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (dirty != null) 'dirty': dirty,
      if (pendingDelete != null) 'pending_delete': pendingDelete,
      if (rowid != null) 'rowid': rowid,
    });
  }

  TaskRowsCompanion copyWith({
    Value<String>? slug,
    Value<String>? title,
    Value<String>? status,
    Value<String>? projectSlug,
    Value<String>? assignee,
    Value<String>? priority,
    Value<String>? payloadJson,
    Value<String>? updatedAt,
    Value<bool>? dirty,
    Value<bool>? pendingDelete,
    Value<int>? rowid,
  }) {
    return TaskRowsCompanion(
      slug: slug ?? this.slug,
      title: title ?? this.title,
      status: status ?? this.status,
      projectSlug: projectSlug ?? this.projectSlug,
      assignee: assignee ?? this.assignee,
      priority: priority ?? this.priority,
      payloadJson: payloadJson ?? this.payloadJson,
      updatedAt: updatedAt ?? this.updatedAt,
      dirty: dirty ?? this.dirty,
      pendingDelete: pendingDelete ?? this.pendingDelete,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (slug.present) {
      map['slug'] = Variable<String>(slug.value);
    }
    if (title.present) {
      map['title'] = Variable<String>(title.value);
    }
    if (status.present) {
      map['status'] = Variable<String>(status.value);
    }
    if (projectSlug.present) {
      map['project_slug'] = Variable<String>(projectSlug.value);
    }
    if (assignee.present) {
      map['assignee'] = Variable<String>(assignee.value);
    }
    if (priority.present) {
      map['priority'] = Variable<String>(priority.value);
    }
    if (payloadJson.present) {
      map['payload_json'] = Variable<String>(payloadJson.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<String>(updatedAt.value);
    }
    if (dirty.present) {
      map['dirty'] = Variable<bool>(dirty.value);
    }
    if (pendingDelete.present) {
      map['pending_delete'] = Variable<bool>(pendingDelete.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('TaskRowsCompanion(')
          ..write('slug: $slug, ')
          ..write('title: $title, ')
          ..write('status: $status, ')
          ..write('projectSlug: $projectSlug, ')
          ..write('assignee: $assignee, ')
          ..write('priority: $priority, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('dirty: $dirty, ')
          ..write('pendingDelete: $pendingDelete, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $ProjectRowsTable extends ProjectRows
    with TableInfo<$ProjectRowsTable, ProjectRow> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $ProjectRowsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _slugMeta = const VerificationMeta('slug');
  @override
  late final GeneratedColumn<String> slug = GeneratedColumn<String>(
    'slug',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _nameMeta = const VerificationMeta('name');
  @override
  late final GeneratedColumn<String> name = GeneratedColumn<String>(
    'name',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _payloadJsonMeta = const VerificationMeta(
    'payloadJson',
  );
  @override
  late final GeneratedColumn<String> payloadJson = GeneratedColumn<String>(
    'payload_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [slug, name, payloadJson];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'project_rows';
  @override
  VerificationContext validateIntegrity(
    Insertable<ProjectRow> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('slug')) {
      context.handle(
        _slugMeta,
        slug.isAcceptableOrUnknown(data['slug']!, _slugMeta),
      );
    } else if (isInserting) {
      context.missing(_slugMeta);
    }
    if (data.containsKey('name')) {
      context.handle(
        _nameMeta,
        name.isAcceptableOrUnknown(data['name']!, _nameMeta),
      );
    } else if (isInserting) {
      context.missing(_nameMeta);
    }
    if (data.containsKey('payload_json')) {
      context.handle(
        _payloadJsonMeta,
        payloadJson.isAcceptableOrUnknown(
          data['payload_json']!,
          _payloadJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_payloadJsonMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {slug};
  @override
  ProjectRow map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return ProjectRow(
      slug: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}slug'],
      )!,
      name: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}name'],
      )!,
      payloadJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}payload_json'],
      )!,
    );
  }

  @override
  $ProjectRowsTable createAlias(String alias) {
    return $ProjectRowsTable(attachedDatabase, alias);
  }
}

class ProjectRow extends DataClass implements Insertable<ProjectRow> {
  final String slug;
  final String name;
  final String payloadJson;
  const ProjectRow({
    required this.slug,
    required this.name,
    required this.payloadJson,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['slug'] = Variable<String>(slug);
    map['name'] = Variable<String>(name);
    map['payload_json'] = Variable<String>(payloadJson);
    return map;
  }

  ProjectRowsCompanion toCompanion(bool nullToAbsent) {
    return ProjectRowsCompanion(
      slug: Value(slug),
      name: Value(name),
      payloadJson: Value(payloadJson),
    );
  }

  factory ProjectRow.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return ProjectRow(
      slug: serializer.fromJson<String>(json['slug']),
      name: serializer.fromJson<String>(json['name']),
      payloadJson: serializer.fromJson<String>(json['payloadJson']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'slug': serializer.toJson<String>(slug),
      'name': serializer.toJson<String>(name),
      'payloadJson': serializer.toJson<String>(payloadJson),
    };
  }

  ProjectRow copyWith({String? slug, String? name, String? payloadJson}) =>
      ProjectRow(
        slug: slug ?? this.slug,
        name: name ?? this.name,
        payloadJson: payloadJson ?? this.payloadJson,
      );
  ProjectRow copyWithCompanion(ProjectRowsCompanion data) {
    return ProjectRow(
      slug: data.slug.present ? data.slug.value : this.slug,
      name: data.name.present ? data.name.value : this.name,
      payloadJson: data.payloadJson.present
          ? data.payloadJson.value
          : this.payloadJson,
    );
  }

  @override
  String toString() {
    return (StringBuffer('ProjectRow(')
          ..write('slug: $slug, ')
          ..write('name: $name, ')
          ..write('payloadJson: $payloadJson')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(slug, name, payloadJson);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is ProjectRow &&
          other.slug == this.slug &&
          other.name == this.name &&
          other.payloadJson == this.payloadJson);
}

class ProjectRowsCompanion extends UpdateCompanion<ProjectRow> {
  final Value<String> slug;
  final Value<String> name;
  final Value<String> payloadJson;
  final Value<int> rowid;
  const ProjectRowsCompanion({
    this.slug = const Value.absent(),
    this.name = const Value.absent(),
    this.payloadJson = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  ProjectRowsCompanion.insert({
    required String slug,
    required String name,
    required String payloadJson,
    this.rowid = const Value.absent(),
  }) : slug = Value(slug),
       name = Value(name),
       payloadJson = Value(payloadJson);
  static Insertable<ProjectRow> custom({
    Expression<String>? slug,
    Expression<String>? name,
    Expression<String>? payloadJson,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (slug != null) 'slug': slug,
      if (name != null) 'name': name,
      if (payloadJson != null) 'payload_json': payloadJson,
      if (rowid != null) 'rowid': rowid,
    });
  }

  ProjectRowsCompanion copyWith({
    Value<String>? slug,
    Value<String>? name,
    Value<String>? payloadJson,
    Value<int>? rowid,
  }) {
    return ProjectRowsCompanion(
      slug: slug ?? this.slug,
      name: name ?? this.name,
      payloadJson: payloadJson ?? this.payloadJson,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (slug.present) {
      map['slug'] = Variable<String>(slug.value);
    }
    if (name.present) {
      map['name'] = Variable<String>(name.value);
    }
    if (payloadJson.present) {
      map['payload_json'] = Variable<String>(payloadJson.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('ProjectRowsCompanion(')
          ..write('slug: $slug, ')
          ..write('name: $name, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $EscalationRowsTable extends EscalationRows
    with TableInfo<$EscalationRowsTable, EscalationRow> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $EscalationRowsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _slugMeta = const VerificationMeta('slug');
  @override
  late final GeneratedColumn<String> slug = GeneratedColumn<String>(
    'slug',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _resolvedMeta = const VerificationMeta(
    'resolved',
  );
  @override
  late final GeneratedColumn<bool> resolved = GeneratedColumn<bool>(
    'resolved',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("resolved" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _payloadJsonMeta = const VerificationMeta(
    'payloadJson',
  );
  @override
  late final GeneratedColumn<String> payloadJson = GeneratedColumn<String>(
    'payload_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<String> createdAt = GeneratedColumn<String>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _dirtyMeta = const VerificationMeta('dirty');
  @override
  late final GeneratedColumn<bool> dirty = GeneratedColumn<bool>(
    'dirty',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("dirty" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  @override
  List<GeneratedColumn> get $columns => [
    slug,
    resolved,
    payloadJson,
    createdAt,
    dirty,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'escalation_rows';
  @override
  VerificationContext validateIntegrity(
    Insertable<EscalationRow> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('slug')) {
      context.handle(
        _slugMeta,
        slug.isAcceptableOrUnknown(data['slug']!, _slugMeta),
      );
    } else if (isInserting) {
      context.missing(_slugMeta);
    }
    if (data.containsKey('resolved')) {
      context.handle(
        _resolvedMeta,
        resolved.isAcceptableOrUnknown(data['resolved']!, _resolvedMeta),
      );
    }
    if (data.containsKey('payload_json')) {
      context.handle(
        _payloadJsonMeta,
        payloadJson.isAcceptableOrUnknown(
          data['payload_json']!,
          _payloadJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_payloadJsonMeta);
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('dirty')) {
      context.handle(
        _dirtyMeta,
        dirty.isAcceptableOrUnknown(data['dirty']!, _dirtyMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {slug};
  @override
  EscalationRow map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return EscalationRow(
      slug: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}slug'],
      )!,
      resolved: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}resolved'],
      )!,
      payloadJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}payload_json'],
      )!,
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}created_at'],
      )!,
      dirty: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}dirty'],
      )!,
    );
  }

  @override
  $EscalationRowsTable createAlias(String alias) {
    return $EscalationRowsTable(attachedDatabase, alias);
  }
}

class EscalationRow extends DataClass implements Insertable<EscalationRow> {
  final String slug;
  final bool resolved;
  final String payloadJson;
  final String createdAt;
  final bool dirty;
  const EscalationRow({
    required this.slug,
    required this.resolved,
    required this.payloadJson,
    required this.createdAt,
    required this.dirty,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['slug'] = Variable<String>(slug);
    map['resolved'] = Variable<bool>(resolved);
    map['payload_json'] = Variable<String>(payloadJson);
    map['created_at'] = Variable<String>(createdAt);
    map['dirty'] = Variable<bool>(dirty);
    return map;
  }

  EscalationRowsCompanion toCompanion(bool nullToAbsent) {
    return EscalationRowsCompanion(
      slug: Value(slug),
      resolved: Value(resolved),
      payloadJson: Value(payloadJson),
      createdAt: Value(createdAt),
      dirty: Value(dirty),
    );
  }

  factory EscalationRow.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return EscalationRow(
      slug: serializer.fromJson<String>(json['slug']),
      resolved: serializer.fromJson<bool>(json['resolved']),
      payloadJson: serializer.fromJson<String>(json['payloadJson']),
      createdAt: serializer.fromJson<String>(json['createdAt']),
      dirty: serializer.fromJson<bool>(json['dirty']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'slug': serializer.toJson<String>(slug),
      'resolved': serializer.toJson<bool>(resolved),
      'payloadJson': serializer.toJson<String>(payloadJson),
      'createdAt': serializer.toJson<String>(createdAt),
      'dirty': serializer.toJson<bool>(dirty),
    };
  }

  EscalationRow copyWith({
    String? slug,
    bool? resolved,
    String? payloadJson,
    String? createdAt,
    bool? dirty,
  }) => EscalationRow(
    slug: slug ?? this.slug,
    resolved: resolved ?? this.resolved,
    payloadJson: payloadJson ?? this.payloadJson,
    createdAt: createdAt ?? this.createdAt,
    dirty: dirty ?? this.dirty,
  );
  EscalationRow copyWithCompanion(EscalationRowsCompanion data) {
    return EscalationRow(
      slug: data.slug.present ? data.slug.value : this.slug,
      resolved: data.resolved.present ? data.resolved.value : this.resolved,
      payloadJson: data.payloadJson.present
          ? data.payloadJson.value
          : this.payloadJson,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      dirty: data.dirty.present ? data.dirty.value : this.dirty,
    );
  }

  @override
  String toString() {
    return (StringBuffer('EscalationRow(')
          ..write('slug: $slug, ')
          ..write('resolved: $resolved, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('createdAt: $createdAt, ')
          ..write('dirty: $dirty')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode =>
      Object.hash(slug, resolved, payloadJson, createdAt, dirty);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is EscalationRow &&
          other.slug == this.slug &&
          other.resolved == this.resolved &&
          other.payloadJson == this.payloadJson &&
          other.createdAt == this.createdAt &&
          other.dirty == this.dirty);
}

class EscalationRowsCompanion extends UpdateCompanion<EscalationRow> {
  final Value<String> slug;
  final Value<bool> resolved;
  final Value<String> payloadJson;
  final Value<String> createdAt;
  final Value<bool> dirty;
  final Value<int> rowid;
  const EscalationRowsCompanion({
    this.slug = const Value.absent(),
    this.resolved = const Value.absent(),
    this.payloadJson = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.dirty = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  EscalationRowsCompanion.insert({
    required String slug,
    this.resolved = const Value.absent(),
    required String payloadJson,
    required String createdAt,
    this.dirty = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : slug = Value(slug),
       payloadJson = Value(payloadJson),
       createdAt = Value(createdAt);
  static Insertable<EscalationRow> custom({
    Expression<String>? slug,
    Expression<bool>? resolved,
    Expression<String>? payloadJson,
    Expression<String>? createdAt,
    Expression<bool>? dirty,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (slug != null) 'slug': slug,
      if (resolved != null) 'resolved': resolved,
      if (payloadJson != null) 'payload_json': payloadJson,
      if (createdAt != null) 'created_at': createdAt,
      if (dirty != null) 'dirty': dirty,
      if (rowid != null) 'rowid': rowid,
    });
  }

  EscalationRowsCompanion copyWith({
    Value<String>? slug,
    Value<bool>? resolved,
    Value<String>? payloadJson,
    Value<String>? createdAt,
    Value<bool>? dirty,
    Value<int>? rowid,
  }) {
    return EscalationRowsCompanion(
      slug: slug ?? this.slug,
      resolved: resolved ?? this.resolved,
      payloadJson: payloadJson ?? this.payloadJson,
      createdAt: createdAt ?? this.createdAt,
      dirty: dirty ?? this.dirty,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (slug.present) {
      map['slug'] = Variable<String>(slug.value);
    }
    if (resolved.present) {
      map['resolved'] = Variable<bool>(resolved.value);
    }
    if (payloadJson.present) {
      map['payload_json'] = Variable<String>(payloadJson.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<String>(createdAt.value);
    }
    if (dirty.present) {
      map['dirty'] = Variable<bool>(dirty.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('EscalationRowsCompanion(')
          ..write('slug: $slug, ')
          ..write('resolved: $resolved, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('createdAt: $createdAt, ')
          ..write('dirty: $dirty, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $ChatMessageRowsTable extends ChatMessageRows
    with TableInfo<$ChatMessageRowsTable, ChatMessageRow> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $ChatMessageRowsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _botMeta = const VerificationMeta('bot');
  @override
  late final GeneratedColumn<String> bot = GeneratedColumn<String>(
    'bot',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _roleMeta = const VerificationMeta('role');
  @override
  late final GeneratedColumn<String> role = GeneratedColumn<String>(
    'role',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _payloadJsonMeta = const VerificationMeta(
    'payloadJson',
  );
  @override
  late final GeneratedColumn<String> payloadJson = GeneratedColumn<String>(
    'payload_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _tsMeta = const VerificationMeta('ts');
  @override
  late final GeneratedColumn<String> ts = GeneratedColumn<String>(
    'ts',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _dirtyMeta = const VerificationMeta('dirty');
  @override
  late final GeneratedColumn<bool> dirty = GeneratedColumn<bool>(
    'dirty',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("dirty" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  @override
  List<GeneratedColumn> get $columns => [id, bot, role, payloadJson, ts, dirty];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'chat_message_rows';
  @override
  VerificationContext validateIntegrity(
    Insertable<ChatMessageRow> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('bot')) {
      context.handle(
        _botMeta,
        bot.isAcceptableOrUnknown(data['bot']!, _botMeta),
      );
    } else if (isInserting) {
      context.missing(_botMeta);
    }
    if (data.containsKey('role')) {
      context.handle(
        _roleMeta,
        role.isAcceptableOrUnknown(data['role']!, _roleMeta),
      );
    } else if (isInserting) {
      context.missing(_roleMeta);
    }
    if (data.containsKey('payload_json')) {
      context.handle(
        _payloadJsonMeta,
        payloadJson.isAcceptableOrUnknown(
          data['payload_json']!,
          _payloadJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_payloadJsonMeta);
    }
    if (data.containsKey('ts')) {
      context.handle(_tsMeta, ts.isAcceptableOrUnknown(data['ts']!, _tsMeta));
    } else if (isInserting) {
      context.missing(_tsMeta);
    }
    if (data.containsKey('dirty')) {
      context.handle(
        _dirtyMeta,
        dirty.isAcceptableOrUnknown(data['dirty']!, _dirtyMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  ChatMessageRow map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return ChatMessageRow(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      bot: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}bot'],
      )!,
      role: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}role'],
      )!,
      payloadJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}payload_json'],
      )!,
      ts: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}ts'],
      )!,
      dirty: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}dirty'],
      )!,
    );
  }

  @override
  $ChatMessageRowsTable createAlias(String alias) {
    return $ChatMessageRowsTable(attachedDatabase, alias);
  }
}

class ChatMessageRow extends DataClass implements Insertable<ChatMessageRow> {
  final String id;
  final String bot;
  final String role;
  final String payloadJson;
  final String ts;
  final bool dirty;
  const ChatMessageRow({
    required this.id,
    required this.bot,
    required this.role,
    required this.payloadJson,
    required this.ts,
    required this.dirty,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    map['bot'] = Variable<String>(bot);
    map['role'] = Variable<String>(role);
    map['payload_json'] = Variable<String>(payloadJson);
    map['ts'] = Variable<String>(ts);
    map['dirty'] = Variable<bool>(dirty);
    return map;
  }

  ChatMessageRowsCompanion toCompanion(bool nullToAbsent) {
    return ChatMessageRowsCompanion(
      id: Value(id),
      bot: Value(bot),
      role: Value(role),
      payloadJson: Value(payloadJson),
      ts: Value(ts),
      dirty: Value(dirty),
    );
  }

  factory ChatMessageRow.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return ChatMessageRow(
      id: serializer.fromJson<String>(json['id']),
      bot: serializer.fromJson<String>(json['bot']),
      role: serializer.fromJson<String>(json['role']),
      payloadJson: serializer.fromJson<String>(json['payloadJson']),
      ts: serializer.fromJson<String>(json['ts']),
      dirty: serializer.fromJson<bool>(json['dirty']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'bot': serializer.toJson<String>(bot),
      'role': serializer.toJson<String>(role),
      'payloadJson': serializer.toJson<String>(payloadJson),
      'ts': serializer.toJson<String>(ts),
      'dirty': serializer.toJson<bool>(dirty),
    };
  }

  ChatMessageRow copyWith({
    String? id,
    String? bot,
    String? role,
    String? payloadJson,
    String? ts,
    bool? dirty,
  }) => ChatMessageRow(
    id: id ?? this.id,
    bot: bot ?? this.bot,
    role: role ?? this.role,
    payloadJson: payloadJson ?? this.payloadJson,
    ts: ts ?? this.ts,
    dirty: dirty ?? this.dirty,
  );
  ChatMessageRow copyWithCompanion(ChatMessageRowsCompanion data) {
    return ChatMessageRow(
      id: data.id.present ? data.id.value : this.id,
      bot: data.bot.present ? data.bot.value : this.bot,
      role: data.role.present ? data.role.value : this.role,
      payloadJson: data.payloadJson.present
          ? data.payloadJson.value
          : this.payloadJson,
      ts: data.ts.present ? data.ts.value : this.ts,
      dirty: data.dirty.present ? data.dirty.value : this.dirty,
    );
  }

  @override
  String toString() {
    return (StringBuffer('ChatMessageRow(')
          ..write('id: $id, ')
          ..write('bot: $bot, ')
          ..write('role: $role, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('ts: $ts, ')
          ..write('dirty: $dirty')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(id, bot, role, payloadJson, ts, dirty);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is ChatMessageRow &&
          other.id == this.id &&
          other.bot == this.bot &&
          other.role == this.role &&
          other.payloadJson == this.payloadJson &&
          other.ts == this.ts &&
          other.dirty == this.dirty);
}

class ChatMessageRowsCompanion extends UpdateCompanion<ChatMessageRow> {
  final Value<String> id;
  final Value<String> bot;
  final Value<String> role;
  final Value<String> payloadJson;
  final Value<String> ts;
  final Value<bool> dirty;
  final Value<int> rowid;
  const ChatMessageRowsCompanion({
    this.id = const Value.absent(),
    this.bot = const Value.absent(),
    this.role = const Value.absent(),
    this.payloadJson = const Value.absent(),
    this.ts = const Value.absent(),
    this.dirty = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  ChatMessageRowsCompanion.insert({
    required String id,
    required String bot,
    required String role,
    required String payloadJson,
    required String ts,
    this.dirty = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       bot = Value(bot),
       role = Value(role),
       payloadJson = Value(payloadJson),
       ts = Value(ts);
  static Insertable<ChatMessageRow> custom({
    Expression<String>? id,
    Expression<String>? bot,
    Expression<String>? role,
    Expression<String>? payloadJson,
    Expression<String>? ts,
    Expression<bool>? dirty,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (bot != null) 'bot': bot,
      if (role != null) 'role': role,
      if (payloadJson != null) 'payload_json': payloadJson,
      if (ts != null) 'ts': ts,
      if (dirty != null) 'dirty': dirty,
      if (rowid != null) 'rowid': rowid,
    });
  }

  ChatMessageRowsCompanion copyWith({
    Value<String>? id,
    Value<String>? bot,
    Value<String>? role,
    Value<String>? payloadJson,
    Value<String>? ts,
    Value<bool>? dirty,
    Value<int>? rowid,
  }) {
    return ChatMessageRowsCompanion(
      id: id ?? this.id,
      bot: bot ?? this.bot,
      role: role ?? this.role,
      payloadJson: payloadJson ?? this.payloadJson,
      ts: ts ?? this.ts,
      dirty: dirty ?? this.dirty,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (bot.present) {
      map['bot'] = Variable<String>(bot.value);
    }
    if (role.present) {
      map['role'] = Variable<String>(role.value);
    }
    if (payloadJson.present) {
      map['payload_json'] = Variable<String>(payloadJson.value);
    }
    if (ts.present) {
      map['ts'] = Variable<String>(ts.value);
    }
    if (dirty.present) {
      map['dirty'] = Variable<bool>(dirty.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('ChatMessageRowsCompanion(')
          ..write('id: $id, ')
          ..write('bot: $bot, ')
          ..write('role: $role, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('ts: $ts, ')
          ..write('dirty: $dirty, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $ActivityRowsTable extends ActivityRows
    with TableInfo<$ActivityRowsTable, ActivityRow> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $ActivityRowsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<int> id = GeneratedColumn<int>(
    'id',
    aliasedName,
    false,
    hasAutoIncrement: true,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'PRIMARY KEY AUTOINCREMENT',
    ),
  );
  static const VerificationMeta _kindMeta = const VerificationMeta('kind');
  @override
  late final GeneratedColumn<String> kind = GeneratedColumn<String>(
    'kind',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _payloadJsonMeta = const VerificationMeta(
    'payloadJson',
  );
  @override
  late final GeneratedColumn<String> payloadJson = GeneratedColumn<String>(
    'payload_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _tsMeta = const VerificationMeta('ts');
  @override
  late final GeneratedColumn<String> ts = GeneratedColumn<String>(
    'ts',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [id, kind, payloadJson, ts];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'activity_rows';
  @override
  VerificationContext validateIntegrity(
    Insertable<ActivityRow> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    }
    if (data.containsKey('kind')) {
      context.handle(
        _kindMeta,
        kind.isAcceptableOrUnknown(data['kind']!, _kindMeta),
      );
    } else if (isInserting) {
      context.missing(_kindMeta);
    }
    if (data.containsKey('payload_json')) {
      context.handle(
        _payloadJsonMeta,
        payloadJson.isAcceptableOrUnknown(
          data['payload_json']!,
          _payloadJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_payloadJsonMeta);
    }
    if (data.containsKey('ts')) {
      context.handle(_tsMeta, ts.isAcceptableOrUnknown(data['ts']!, _tsMeta));
    } else if (isInserting) {
      context.missing(_tsMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  ActivityRow map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return ActivityRow(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}id'],
      )!,
      kind: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}kind'],
      )!,
      payloadJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}payload_json'],
      )!,
      ts: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}ts'],
      )!,
    );
  }

  @override
  $ActivityRowsTable createAlias(String alias) {
    return $ActivityRowsTable(attachedDatabase, alias);
  }
}

class ActivityRow extends DataClass implements Insertable<ActivityRow> {
  final int id;
  final String kind;
  final String payloadJson;
  final String ts;
  const ActivityRow({
    required this.id,
    required this.kind,
    required this.payloadJson,
    required this.ts,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<int>(id);
    map['kind'] = Variable<String>(kind);
    map['payload_json'] = Variable<String>(payloadJson);
    map['ts'] = Variable<String>(ts);
    return map;
  }

  ActivityRowsCompanion toCompanion(bool nullToAbsent) {
    return ActivityRowsCompanion(
      id: Value(id),
      kind: Value(kind),
      payloadJson: Value(payloadJson),
      ts: Value(ts),
    );
  }

  factory ActivityRow.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return ActivityRow(
      id: serializer.fromJson<int>(json['id']),
      kind: serializer.fromJson<String>(json['kind']),
      payloadJson: serializer.fromJson<String>(json['payloadJson']),
      ts: serializer.fromJson<String>(json['ts']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<int>(id),
      'kind': serializer.toJson<String>(kind),
      'payloadJson': serializer.toJson<String>(payloadJson),
      'ts': serializer.toJson<String>(ts),
    };
  }

  ActivityRow copyWith({
    int? id,
    String? kind,
    String? payloadJson,
    String? ts,
  }) => ActivityRow(
    id: id ?? this.id,
    kind: kind ?? this.kind,
    payloadJson: payloadJson ?? this.payloadJson,
    ts: ts ?? this.ts,
  );
  ActivityRow copyWithCompanion(ActivityRowsCompanion data) {
    return ActivityRow(
      id: data.id.present ? data.id.value : this.id,
      kind: data.kind.present ? data.kind.value : this.kind,
      payloadJson: data.payloadJson.present
          ? data.payloadJson.value
          : this.payloadJson,
      ts: data.ts.present ? data.ts.value : this.ts,
    );
  }

  @override
  String toString() {
    return (StringBuffer('ActivityRow(')
          ..write('id: $id, ')
          ..write('kind: $kind, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('ts: $ts')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(id, kind, payloadJson, ts);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is ActivityRow &&
          other.id == this.id &&
          other.kind == this.kind &&
          other.payloadJson == this.payloadJson &&
          other.ts == this.ts);
}

class ActivityRowsCompanion extends UpdateCompanion<ActivityRow> {
  final Value<int> id;
  final Value<String> kind;
  final Value<String> payloadJson;
  final Value<String> ts;
  const ActivityRowsCompanion({
    this.id = const Value.absent(),
    this.kind = const Value.absent(),
    this.payloadJson = const Value.absent(),
    this.ts = const Value.absent(),
  });
  ActivityRowsCompanion.insert({
    this.id = const Value.absent(),
    required String kind,
    required String payloadJson,
    required String ts,
  }) : kind = Value(kind),
       payloadJson = Value(payloadJson),
       ts = Value(ts);
  static Insertable<ActivityRow> custom({
    Expression<int>? id,
    Expression<String>? kind,
    Expression<String>? payloadJson,
    Expression<String>? ts,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (kind != null) 'kind': kind,
      if (payloadJson != null) 'payload_json': payloadJson,
      if (ts != null) 'ts': ts,
    });
  }

  ActivityRowsCompanion copyWith({
    Value<int>? id,
    Value<String>? kind,
    Value<String>? payloadJson,
    Value<String>? ts,
  }) {
    return ActivityRowsCompanion(
      id: id ?? this.id,
      kind: kind ?? this.kind,
      payloadJson: payloadJson ?? this.payloadJson,
      ts: ts ?? this.ts,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<int>(id.value);
    }
    if (kind.present) {
      map['kind'] = Variable<String>(kind.value);
    }
    if (payloadJson.present) {
      map['payload_json'] = Variable<String>(payloadJson.value);
    }
    if (ts.present) {
      map['ts'] = Variable<String>(ts.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('ActivityRowsCompanion(')
          ..write('id: $id, ')
          ..write('kind: $kind, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('ts: $ts')
          ..write(')'))
        .toString();
  }
}

class $SyncStateRowsTable extends SyncStateRows
    with TableInfo<$SyncStateRowsTable, SyncStateRow> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $SyncStateRowsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<int> id = GeneratedColumn<int>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _lastPullJsonMeta = const VerificationMeta(
    'lastPullJson',
  );
  @override
  late final GeneratedColumn<String> lastPullJson = GeneratedColumn<String>(
    'last_pull_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
    defaultValue: const Constant('{}'),
  );
  static const VerificationMeta _lastSeenJsonMeta = const VerificationMeta(
    'lastSeenJson',
  );
  @override
  late final GeneratedColumn<String> lastSeenJson = GeneratedColumn<String>(
    'last_seen_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
    defaultValue: const Constant('{}'),
  );
  @override
  List<GeneratedColumn> get $columns => [id, lastPullJson, lastSeenJson];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'sync_state_rows';
  @override
  VerificationContext validateIntegrity(
    Insertable<SyncStateRow> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    }
    if (data.containsKey('last_pull_json')) {
      context.handle(
        _lastPullJsonMeta,
        lastPullJson.isAcceptableOrUnknown(
          data['last_pull_json']!,
          _lastPullJsonMeta,
        ),
      );
    }
    if (data.containsKey('last_seen_json')) {
      context.handle(
        _lastSeenJsonMeta,
        lastSeenJson.isAcceptableOrUnknown(
          data['last_seen_json']!,
          _lastSeenJsonMeta,
        ),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  SyncStateRow map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return SyncStateRow(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}id'],
      )!,
      lastPullJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}last_pull_json'],
      )!,
      lastSeenJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}last_seen_json'],
      )!,
    );
  }

  @override
  $SyncStateRowsTable createAlias(String alias) {
    return $SyncStateRowsTable(attachedDatabase, alias);
  }
}

class SyncStateRow extends DataClass implements Insertable<SyncStateRow> {
  final int id;
  final String lastPullJson;
  final String lastSeenJson;
  const SyncStateRow({
    required this.id,
    required this.lastPullJson,
    required this.lastSeenJson,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<int>(id);
    map['last_pull_json'] = Variable<String>(lastPullJson);
    map['last_seen_json'] = Variable<String>(lastSeenJson);
    return map;
  }

  SyncStateRowsCompanion toCompanion(bool nullToAbsent) {
    return SyncStateRowsCompanion(
      id: Value(id),
      lastPullJson: Value(lastPullJson),
      lastSeenJson: Value(lastSeenJson),
    );
  }

  factory SyncStateRow.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return SyncStateRow(
      id: serializer.fromJson<int>(json['id']),
      lastPullJson: serializer.fromJson<String>(json['lastPullJson']),
      lastSeenJson: serializer.fromJson<String>(json['lastSeenJson']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<int>(id),
      'lastPullJson': serializer.toJson<String>(lastPullJson),
      'lastSeenJson': serializer.toJson<String>(lastSeenJson),
    };
  }

  SyncStateRow copyWith({
    int? id,
    String? lastPullJson,
    String? lastSeenJson,
  }) => SyncStateRow(
    id: id ?? this.id,
    lastPullJson: lastPullJson ?? this.lastPullJson,
    lastSeenJson: lastSeenJson ?? this.lastSeenJson,
  );
  SyncStateRow copyWithCompanion(SyncStateRowsCompanion data) {
    return SyncStateRow(
      id: data.id.present ? data.id.value : this.id,
      lastPullJson: data.lastPullJson.present
          ? data.lastPullJson.value
          : this.lastPullJson,
      lastSeenJson: data.lastSeenJson.present
          ? data.lastSeenJson.value
          : this.lastSeenJson,
    );
  }

  @override
  String toString() {
    return (StringBuffer('SyncStateRow(')
          ..write('id: $id, ')
          ..write('lastPullJson: $lastPullJson, ')
          ..write('lastSeenJson: $lastSeenJson')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(id, lastPullJson, lastSeenJson);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is SyncStateRow &&
          other.id == this.id &&
          other.lastPullJson == this.lastPullJson &&
          other.lastSeenJson == this.lastSeenJson);
}

class SyncStateRowsCompanion extends UpdateCompanion<SyncStateRow> {
  final Value<int> id;
  final Value<String> lastPullJson;
  final Value<String> lastSeenJson;
  const SyncStateRowsCompanion({
    this.id = const Value.absent(),
    this.lastPullJson = const Value.absent(),
    this.lastSeenJson = const Value.absent(),
  });
  SyncStateRowsCompanion.insert({
    this.id = const Value.absent(),
    this.lastPullJson = const Value.absent(),
    this.lastSeenJson = const Value.absent(),
  });
  static Insertable<SyncStateRow> custom({
    Expression<int>? id,
    Expression<String>? lastPullJson,
    Expression<String>? lastSeenJson,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (lastPullJson != null) 'last_pull_json': lastPullJson,
      if (lastSeenJson != null) 'last_seen_json': lastSeenJson,
    });
  }

  SyncStateRowsCompanion copyWith({
    Value<int>? id,
    Value<String>? lastPullJson,
    Value<String>? lastSeenJson,
  }) {
    return SyncStateRowsCompanion(
      id: id ?? this.id,
      lastPullJson: lastPullJson ?? this.lastPullJson,
      lastSeenJson: lastSeenJson ?? this.lastSeenJson,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<int>(id.value);
    }
    if (lastPullJson.present) {
      map['last_pull_json'] = Variable<String>(lastPullJson.value);
    }
    if (lastSeenJson.present) {
      map['last_seen_json'] = Variable<String>(lastSeenJson.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('SyncStateRowsCompanion(')
          ..write('id: $id, ')
          ..write('lastPullJson: $lastPullJson, ')
          ..write('lastSeenJson: $lastSeenJson')
          ..write(')'))
        .toString();
  }
}

class $OutboxRowsTable extends OutboxRows
    with TableInfo<$OutboxRowsTable, OutboxRow> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $OutboxRowsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<int> id = GeneratedColumn<int>(
    'id',
    aliasedName,
    false,
    hasAutoIncrement: true,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'PRIMARY KEY AUTOINCREMENT',
    ),
  );
  static const VerificationMeta _opMeta = const VerificationMeta('op');
  @override
  late final GeneratedColumn<String> op = GeneratedColumn<String>(
    'op',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _targetIdMeta = const VerificationMeta(
    'targetId',
  );
  @override
  late final GeneratedColumn<String> targetId = GeneratedColumn<String>(
    'target_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _payloadJsonMeta = const VerificationMeta(
    'payloadJson',
  );
  @override
  late final GeneratedColumn<String> payloadJson = GeneratedColumn<String>(
    'payload_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<int> createdAt = GeneratedColumn<int>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _attemptsMeta = const VerificationMeta(
    'attempts',
  );
  @override
  late final GeneratedColumn<int> attempts = GeneratedColumn<int>(
    'attempts',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultValue: const Constant(0),
  );
  static const VerificationMeta _lastErrorMeta = const VerificationMeta(
    'lastError',
  );
  @override
  late final GeneratedColumn<String> lastError = GeneratedColumn<String>(
    'last_error',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _statusMeta = const VerificationMeta('status');
  @override
  late final GeneratedColumn<String> status = GeneratedColumn<String>(
    'status',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
    defaultValue: const Constant('pending'),
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    op,
    targetId,
    payloadJson,
    createdAt,
    attempts,
    lastError,
    status,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'outbox_rows';
  @override
  VerificationContext validateIntegrity(
    Insertable<OutboxRow> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    }
    if (data.containsKey('op')) {
      context.handle(_opMeta, op.isAcceptableOrUnknown(data['op']!, _opMeta));
    } else if (isInserting) {
      context.missing(_opMeta);
    }
    if (data.containsKey('target_id')) {
      context.handle(
        _targetIdMeta,
        targetId.isAcceptableOrUnknown(data['target_id']!, _targetIdMeta),
      );
    } else if (isInserting) {
      context.missing(_targetIdMeta);
    }
    if (data.containsKey('payload_json')) {
      context.handle(
        _payloadJsonMeta,
        payloadJson.isAcceptableOrUnknown(
          data['payload_json']!,
          _payloadJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_payloadJsonMeta);
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('attempts')) {
      context.handle(
        _attemptsMeta,
        attempts.isAcceptableOrUnknown(data['attempts']!, _attemptsMeta),
      );
    }
    if (data.containsKey('last_error')) {
      context.handle(
        _lastErrorMeta,
        lastError.isAcceptableOrUnknown(data['last_error']!, _lastErrorMeta),
      );
    }
    if (data.containsKey('status')) {
      context.handle(
        _statusMeta,
        status.isAcceptableOrUnknown(data['status']!, _statusMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  OutboxRow map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return OutboxRow(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}id'],
      )!,
      op: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}op'],
      )!,
      targetId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}target_id'],
      )!,
      payloadJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}payload_json'],
      )!,
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}created_at'],
      )!,
      attempts: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}attempts'],
      )!,
      lastError: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}last_error'],
      ),
      status: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}status'],
      )!,
    );
  }

  @override
  $OutboxRowsTable createAlias(String alias) {
    return $OutboxRowsTable(attachedDatabase, alias);
  }
}

class OutboxRow extends DataClass implements Insertable<OutboxRow> {
  final int id;
  final String op;
  final String targetId;
  final String payloadJson;
  final int createdAt;
  final int attempts;
  final String? lastError;
  final String status;
  const OutboxRow({
    required this.id,
    required this.op,
    required this.targetId,
    required this.payloadJson,
    required this.createdAt,
    required this.attempts,
    this.lastError,
    required this.status,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<int>(id);
    map['op'] = Variable<String>(op);
    map['target_id'] = Variable<String>(targetId);
    map['payload_json'] = Variable<String>(payloadJson);
    map['created_at'] = Variable<int>(createdAt);
    map['attempts'] = Variable<int>(attempts);
    if (!nullToAbsent || lastError != null) {
      map['last_error'] = Variable<String>(lastError);
    }
    map['status'] = Variable<String>(status);
    return map;
  }

  OutboxRowsCompanion toCompanion(bool nullToAbsent) {
    return OutboxRowsCompanion(
      id: Value(id),
      op: Value(op),
      targetId: Value(targetId),
      payloadJson: Value(payloadJson),
      createdAt: Value(createdAt),
      attempts: Value(attempts),
      lastError: lastError == null && nullToAbsent
          ? const Value.absent()
          : Value(lastError),
      status: Value(status),
    );
  }

  factory OutboxRow.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return OutboxRow(
      id: serializer.fromJson<int>(json['id']),
      op: serializer.fromJson<String>(json['op']),
      targetId: serializer.fromJson<String>(json['targetId']),
      payloadJson: serializer.fromJson<String>(json['payloadJson']),
      createdAt: serializer.fromJson<int>(json['createdAt']),
      attempts: serializer.fromJson<int>(json['attempts']),
      lastError: serializer.fromJson<String?>(json['lastError']),
      status: serializer.fromJson<String>(json['status']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<int>(id),
      'op': serializer.toJson<String>(op),
      'targetId': serializer.toJson<String>(targetId),
      'payloadJson': serializer.toJson<String>(payloadJson),
      'createdAt': serializer.toJson<int>(createdAt),
      'attempts': serializer.toJson<int>(attempts),
      'lastError': serializer.toJson<String?>(lastError),
      'status': serializer.toJson<String>(status),
    };
  }

  OutboxRow copyWith({
    int? id,
    String? op,
    String? targetId,
    String? payloadJson,
    int? createdAt,
    int? attempts,
    Value<String?> lastError = const Value.absent(),
    String? status,
  }) => OutboxRow(
    id: id ?? this.id,
    op: op ?? this.op,
    targetId: targetId ?? this.targetId,
    payloadJson: payloadJson ?? this.payloadJson,
    createdAt: createdAt ?? this.createdAt,
    attempts: attempts ?? this.attempts,
    lastError: lastError.present ? lastError.value : this.lastError,
    status: status ?? this.status,
  );
  OutboxRow copyWithCompanion(OutboxRowsCompanion data) {
    return OutboxRow(
      id: data.id.present ? data.id.value : this.id,
      op: data.op.present ? data.op.value : this.op,
      targetId: data.targetId.present ? data.targetId.value : this.targetId,
      payloadJson: data.payloadJson.present
          ? data.payloadJson.value
          : this.payloadJson,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      attempts: data.attempts.present ? data.attempts.value : this.attempts,
      lastError: data.lastError.present ? data.lastError.value : this.lastError,
      status: data.status.present ? data.status.value : this.status,
    );
  }

  @override
  String toString() {
    return (StringBuffer('OutboxRow(')
          ..write('id: $id, ')
          ..write('op: $op, ')
          ..write('targetId: $targetId, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('createdAt: $createdAt, ')
          ..write('attempts: $attempts, ')
          ..write('lastError: $lastError, ')
          ..write('status: $status')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    op,
    targetId,
    payloadJson,
    createdAt,
    attempts,
    lastError,
    status,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is OutboxRow &&
          other.id == this.id &&
          other.op == this.op &&
          other.targetId == this.targetId &&
          other.payloadJson == this.payloadJson &&
          other.createdAt == this.createdAt &&
          other.attempts == this.attempts &&
          other.lastError == this.lastError &&
          other.status == this.status);
}

class OutboxRowsCompanion extends UpdateCompanion<OutboxRow> {
  final Value<int> id;
  final Value<String> op;
  final Value<String> targetId;
  final Value<String> payloadJson;
  final Value<int> createdAt;
  final Value<int> attempts;
  final Value<String?> lastError;
  final Value<String> status;
  const OutboxRowsCompanion({
    this.id = const Value.absent(),
    this.op = const Value.absent(),
    this.targetId = const Value.absent(),
    this.payloadJson = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.attempts = const Value.absent(),
    this.lastError = const Value.absent(),
    this.status = const Value.absent(),
  });
  OutboxRowsCompanion.insert({
    this.id = const Value.absent(),
    required String op,
    required String targetId,
    required String payloadJson,
    required int createdAt,
    this.attempts = const Value.absent(),
    this.lastError = const Value.absent(),
    this.status = const Value.absent(),
  }) : op = Value(op),
       targetId = Value(targetId),
       payloadJson = Value(payloadJson),
       createdAt = Value(createdAt);
  static Insertable<OutboxRow> custom({
    Expression<int>? id,
    Expression<String>? op,
    Expression<String>? targetId,
    Expression<String>? payloadJson,
    Expression<int>? createdAt,
    Expression<int>? attempts,
    Expression<String>? lastError,
    Expression<String>? status,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (op != null) 'op': op,
      if (targetId != null) 'target_id': targetId,
      if (payloadJson != null) 'payload_json': payloadJson,
      if (createdAt != null) 'created_at': createdAt,
      if (attempts != null) 'attempts': attempts,
      if (lastError != null) 'last_error': lastError,
      if (status != null) 'status': status,
    });
  }

  OutboxRowsCompanion copyWith({
    Value<int>? id,
    Value<String>? op,
    Value<String>? targetId,
    Value<String>? payloadJson,
    Value<int>? createdAt,
    Value<int>? attempts,
    Value<String?>? lastError,
    Value<String>? status,
  }) {
    return OutboxRowsCompanion(
      id: id ?? this.id,
      op: op ?? this.op,
      targetId: targetId ?? this.targetId,
      payloadJson: payloadJson ?? this.payloadJson,
      createdAt: createdAt ?? this.createdAt,
      attempts: attempts ?? this.attempts,
      lastError: lastError ?? this.lastError,
      status: status ?? this.status,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<int>(id.value);
    }
    if (op.present) {
      map['op'] = Variable<String>(op.value);
    }
    if (targetId.present) {
      map['target_id'] = Variable<String>(targetId.value);
    }
    if (payloadJson.present) {
      map['payload_json'] = Variable<String>(payloadJson.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<int>(createdAt.value);
    }
    if (attempts.present) {
      map['attempts'] = Variable<int>(attempts.value);
    }
    if (lastError.present) {
      map['last_error'] = Variable<String>(lastError.value);
    }
    if (status.present) {
      map['status'] = Variable<String>(status.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('OutboxRowsCompanion(')
          ..write('id: $id, ')
          ..write('op: $op, ')
          ..write('targetId: $targetId, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('createdAt: $createdAt, ')
          ..write('attempts: $attempts, ')
          ..write('lastError: $lastError, ')
          ..write('status: $status')
          ..write(')'))
        .toString();
  }
}

abstract class _$AppDatabase extends GeneratedDatabase {
  _$AppDatabase(QueryExecutor e) : super(e);
  $AppDatabaseManager get managers => $AppDatabaseManager(this);
  late final $TaskRowsTable taskRows = $TaskRowsTable(this);
  late final $ProjectRowsTable projectRows = $ProjectRowsTable(this);
  late final $EscalationRowsTable escalationRows = $EscalationRowsTable(this);
  late final $ChatMessageRowsTable chatMessageRows = $ChatMessageRowsTable(
    this,
  );
  late final $ActivityRowsTable activityRows = $ActivityRowsTable(this);
  late final $SyncStateRowsTable syncStateRows = $SyncStateRowsTable(this);
  late final $OutboxRowsTable outboxRows = $OutboxRowsTable(this);
  @override
  Iterable<TableInfo<Table, Object?>> get allTables =>
      allSchemaEntities.whereType<TableInfo<Table, Object?>>();
  @override
  List<DatabaseSchemaEntity> get allSchemaEntities => [
    taskRows,
    projectRows,
    escalationRows,
    chatMessageRows,
    activityRows,
    syncStateRows,
    outboxRows,
  ];
}

typedef $$TaskRowsTableCreateCompanionBuilder =
    TaskRowsCompanion Function({
      required String slug,
      required String title,
      required String status,
      required String projectSlug,
      required String assignee,
      required String priority,
      required String payloadJson,
      required String updatedAt,
      Value<bool> dirty,
      Value<bool> pendingDelete,
      Value<int> rowid,
    });
typedef $$TaskRowsTableUpdateCompanionBuilder =
    TaskRowsCompanion Function({
      Value<String> slug,
      Value<String> title,
      Value<String> status,
      Value<String> projectSlug,
      Value<String> assignee,
      Value<String> priority,
      Value<String> payloadJson,
      Value<String> updatedAt,
      Value<bool> dirty,
      Value<bool> pendingDelete,
      Value<int> rowid,
    });

class $$TaskRowsTableFilterComposer
    extends Composer<_$AppDatabase, $TaskRowsTable> {
  $$TaskRowsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get slug => $composableBuilder(
    column: $table.slug,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get title => $composableBuilder(
    column: $table.title,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get projectSlug => $composableBuilder(
    column: $table.projectSlug,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get assignee => $composableBuilder(
    column: $table.assignee,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get priority => $composableBuilder(
    column: $table.priority,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get dirty => $composableBuilder(
    column: $table.dirty,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get pendingDelete => $composableBuilder(
    column: $table.pendingDelete,
    builder: (column) => ColumnFilters(column),
  );
}

class $$TaskRowsTableOrderingComposer
    extends Composer<_$AppDatabase, $TaskRowsTable> {
  $$TaskRowsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get slug => $composableBuilder(
    column: $table.slug,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get title => $composableBuilder(
    column: $table.title,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get projectSlug => $composableBuilder(
    column: $table.projectSlug,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get assignee => $composableBuilder(
    column: $table.assignee,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get priority => $composableBuilder(
    column: $table.priority,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get dirty => $composableBuilder(
    column: $table.dirty,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get pendingDelete => $composableBuilder(
    column: $table.pendingDelete,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$TaskRowsTableAnnotationComposer
    extends Composer<_$AppDatabase, $TaskRowsTable> {
  $$TaskRowsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get slug =>
      $composableBuilder(column: $table.slug, builder: (column) => column);

  GeneratedColumn<String> get title =>
      $composableBuilder(column: $table.title, builder: (column) => column);

  GeneratedColumn<String> get status =>
      $composableBuilder(column: $table.status, builder: (column) => column);

  GeneratedColumn<String> get projectSlug => $composableBuilder(
    column: $table.projectSlug,
    builder: (column) => column,
  );

  GeneratedColumn<String> get assignee =>
      $composableBuilder(column: $table.assignee, builder: (column) => column);

  GeneratedColumn<String> get priority =>
      $composableBuilder(column: $table.priority, builder: (column) => column);

  GeneratedColumn<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => column,
  );

  GeneratedColumn<String> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);

  GeneratedColumn<bool> get dirty =>
      $composableBuilder(column: $table.dirty, builder: (column) => column);

  GeneratedColumn<bool> get pendingDelete => $composableBuilder(
    column: $table.pendingDelete,
    builder: (column) => column,
  );
}

class $$TaskRowsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $TaskRowsTable,
          TaskRow,
          $$TaskRowsTableFilterComposer,
          $$TaskRowsTableOrderingComposer,
          $$TaskRowsTableAnnotationComposer,
          $$TaskRowsTableCreateCompanionBuilder,
          $$TaskRowsTableUpdateCompanionBuilder,
          (TaskRow, BaseReferences<_$AppDatabase, $TaskRowsTable, TaskRow>),
          TaskRow,
          PrefetchHooks Function()
        > {
  $$TaskRowsTableTableManager(_$AppDatabase db, $TaskRowsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$TaskRowsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$TaskRowsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$TaskRowsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> slug = const Value.absent(),
                Value<String> title = const Value.absent(),
                Value<String> status = const Value.absent(),
                Value<String> projectSlug = const Value.absent(),
                Value<String> assignee = const Value.absent(),
                Value<String> priority = const Value.absent(),
                Value<String> payloadJson = const Value.absent(),
                Value<String> updatedAt = const Value.absent(),
                Value<bool> dirty = const Value.absent(),
                Value<bool> pendingDelete = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => TaskRowsCompanion(
                slug: slug,
                title: title,
                status: status,
                projectSlug: projectSlug,
                assignee: assignee,
                priority: priority,
                payloadJson: payloadJson,
                updatedAt: updatedAt,
                dirty: dirty,
                pendingDelete: pendingDelete,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String slug,
                required String title,
                required String status,
                required String projectSlug,
                required String assignee,
                required String priority,
                required String payloadJson,
                required String updatedAt,
                Value<bool> dirty = const Value.absent(),
                Value<bool> pendingDelete = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => TaskRowsCompanion.insert(
                slug: slug,
                title: title,
                status: status,
                projectSlug: projectSlug,
                assignee: assignee,
                priority: priority,
                payloadJson: payloadJson,
                updatedAt: updatedAt,
                dirty: dirty,
                pendingDelete: pendingDelete,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$TaskRowsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $TaskRowsTable,
      TaskRow,
      $$TaskRowsTableFilterComposer,
      $$TaskRowsTableOrderingComposer,
      $$TaskRowsTableAnnotationComposer,
      $$TaskRowsTableCreateCompanionBuilder,
      $$TaskRowsTableUpdateCompanionBuilder,
      (TaskRow, BaseReferences<_$AppDatabase, $TaskRowsTable, TaskRow>),
      TaskRow,
      PrefetchHooks Function()
    >;
typedef $$ProjectRowsTableCreateCompanionBuilder =
    ProjectRowsCompanion Function({
      required String slug,
      required String name,
      required String payloadJson,
      Value<int> rowid,
    });
typedef $$ProjectRowsTableUpdateCompanionBuilder =
    ProjectRowsCompanion Function({
      Value<String> slug,
      Value<String> name,
      Value<String> payloadJson,
      Value<int> rowid,
    });

class $$ProjectRowsTableFilterComposer
    extends Composer<_$AppDatabase, $ProjectRowsTable> {
  $$ProjectRowsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get slug => $composableBuilder(
    column: $table.slug,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get name => $composableBuilder(
    column: $table.name,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnFilters(column),
  );
}

class $$ProjectRowsTableOrderingComposer
    extends Composer<_$AppDatabase, $ProjectRowsTable> {
  $$ProjectRowsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get slug => $composableBuilder(
    column: $table.slug,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get name => $composableBuilder(
    column: $table.name,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$ProjectRowsTableAnnotationComposer
    extends Composer<_$AppDatabase, $ProjectRowsTable> {
  $$ProjectRowsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get slug =>
      $composableBuilder(column: $table.slug, builder: (column) => column);

  GeneratedColumn<String> get name =>
      $composableBuilder(column: $table.name, builder: (column) => column);

  GeneratedColumn<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => column,
  );
}

class $$ProjectRowsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $ProjectRowsTable,
          ProjectRow,
          $$ProjectRowsTableFilterComposer,
          $$ProjectRowsTableOrderingComposer,
          $$ProjectRowsTableAnnotationComposer,
          $$ProjectRowsTableCreateCompanionBuilder,
          $$ProjectRowsTableUpdateCompanionBuilder,
          (
            ProjectRow,
            BaseReferences<_$AppDatabase, $ProjectRowsTable, ProjectRow>,
          ),
          ProjectRow,
          PrefetchHooks Function()
        > {
  $$ProjectRowsTableTableManager(_$AppDatabase db, $ProjectRowsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$ProjectRowsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$ProjectRowsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$ProjectRowsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> slug = const Value.absent(),
                Value<String> name = const Value.absent(),
                Value<String> payloadJson = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => ProjectRowsCompanion(
                slug: slug,
                name: name,
                payloadJson: payloadJson,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String slug,
                required String name,
                required String payloadJson,
                Value<int> rowid = const Value.absent(),
              }) => ProjectRowsCompanion.insert(
                slug: slug,
                name: name,
                payloadJson: payloadJson,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$ProjectRowsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $ProjectRowsTable,
      ProjectRow,
      $$ProjectRowsTableFilterComposer,
      $$ProjectRowsTableOrderingComposer,
      $$ProjectRowsTableAnnotationComposer,
      $$ProjectRowsTableCreateCompanionBuilder,
      $$ProjectRowsTableUpdateCompanionBuilder,
      (
        ProjectRow,
        BaseReferences<_$AppDatabase, $ProjectRowsTable, ProjectRow>,
      ),
      ProjectRow,
      PrefetchHooks Function()
    >;
typedef $$EscalationRowsTableCreateCompanionBuilder =
    EscalationRowsCompanion Function({
      required String slug,
      Value<bool> resolved,
      required String payloadJson,
      required String createdAt,
      Value<bool> dirty,
      Value<int> rowid,
    });
typedef $$EscalationRowsTableUpdateCompanionBuilder =
    EscalationRowsCompanion Function({
      Value<String> slug,
      Value<bool> resolved,
      Value<String> payloadJson,
      Value<String> createdAt,
      Value<bool> dirty,
      Value<int> rowid,
    });

class $$EscalationRowsTableFilterComposer
    extends Composer<_$AppDatabase, $EscalationRowsTable> {
  $$EscalationRowsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get slug => $composableBuilder(
    column: $table.slug,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get resolved => $composableBuilder(
    column: $table.resolved,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get dirty => $composableBuilder(
    column: $table.dirty,
    builder: (column) => ColumnFilters(column),
  );
}

class $$EscalationRowsTableOrderingComposer
    extends Composer<_$AppDatabase, $EscalationRowsTable> {
  $$EscalationRowsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get slug => $composableBuilder(
    column: $table.slug,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get resolved => $composableBuilder(
    column: $table.resolved,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get dirty => $composableBuilder(
    column: $table.dirty,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$EscalationRowsTableAnnotationComposer
    extends Composer<_$AppDatabase, $EscalationRowsTable> {
  $$EscalationRowsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get slug =>
      $composableBuilder(column: $table.slug, builder: (column) => column);

  GeneratedColumn<bool> get resolved =>
      $composableBuilder(column: $table.resolved, builder: (column) => column);

  GeneratedColumn<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => column,
  );

  GeneratedColumn<String> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<bool> get dirty =>
      $composableBuilder(column: $table.dirty, builder: (column) => column);
}

class $$EscalationRowsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $EscalationRowsTable,
          EscalationRow,
          $$EscalationRowsTableFilterComposer,
          $$EscalationRowsTableOrderingComposer,
          $$EscalationRowsTableAnnotationComposer,
          $$EscalationRowsTableCreateCompanionBuilder,
          $$EscalationRowsTableUpdateCompanionBuilder,
          (
            EscalationRow,
            BaseReferences<_$AppDatabase, $EscalationRowsTable, EscalationRow>,
          ),
          EscalationRow,
          PrefetchHooks Function()
        > {
  $$EscalationRowsTableTableManager(
    _$AppDatabase db,
    $EscalationRowsTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$EscalationRowsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$EscalationRowsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$EscalationRowsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> slug = const Value.absent(),
                Value<bool> resolved = const Value.absent(),
                Value<String> payloadJson = const Value.absent(),
                Value<String> createdAt = const Value.absent(),
                Value<bool> dirty = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => EscalationRowsCompanion(
                slug: slug,
                resolved: resolved,
                payloadJson: payloadJson,
                createdAt: createdAt,
                dirty: dirty,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String slug,
                Value<bool> resolved = const Value.absent(),
                required String payloadJson,
                required String createdAt,
                Value<bool> dirty = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => EscalationRowsCompanion.insert(
                slug: slug,
                resolved: resolved,
                payloadJson: payloadJson,
                createdAt: createdAt,
                dirty: dirty,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$EscalationRowsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $EscalationRowsTable,
      EscalationRow,
      $$EscalationRowsTableFilterComposer,
      $$EscalationRowsTableOrderingComposer,
      $$EscalationRowsTableAnnotationComposer,
      $$EscalationRowsTableCreateCompanionBuilder,
      $$EscalationRowsTableUpdateCompanionBuilder,
      (
        EscalationRow,
        BaseReferences<_$AppDatabase, $EscalationRowsTable, EscalationRow>,
      ),
      EscalationRow,
      PrefetchHooks Function()
    >;
typedef $$ChatMessageRowsTableCreateCompanionBuilder =
    ChatMessageRowsCompanion Function({
      required String id,
      required String bot,
      required String role,
      required String payloadJson,
      required String ts,
      Value<bool> dirty,
      Value<int> rowid,
    });
typedef $$ChatMessageRowsTableUpdateCompanionBuilder =
    ChatMessageRowsCompanion Function({
      Value<String> id,
      Value<String> bot,
      Value<String> role,
      Value<String> payloadJson,
      Value<String> ts,
      Value<bool> dirty,
      Value<int> rowid,
    });

class $$ChatMessageRowsTableFilterComposer
    extends Composer<_$AppDatabase, $ChatMessageRowsTable> {
  $$ChatMessageRowsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get bot => $composableBuilder(
    column: $table.bot,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get role => $composableBuilder(
    column: $table.role,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get ts => $composableBuilder(
    column: $table.ts,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get dirty => $composableBuilder(
    column: $table.dirty,
    builder: (column) => ColumnFilters(column),
  );
}

class $$ChatMessageRowsTableOrderingComposer
    extends Composer<_$AppDatabase, $ChatMessageRowsTable> {
  $$ChatMessageRowsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get bot => $composableBuilder(
    column: $table.bot,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get role => $composableBuilder(
    column: $table.role,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get ts => $composableBuilder(
    column: $table.ts,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get dirty => $composableBuilder(
    column: $table.dirty,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$ChatMessageRowsTableAnnotationComposer
    extends Composer<_$AppDatabase, $ChatMessageRowsTable> {
  $$ChatMessageRowsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get bot =>
      $composableBuilder(column: $table.bot, builder: (column) => column);

  GeneratedColumn<String> get role =>
      $composableBuilder(column: $table.role, builder: (column) => column);

  GeneratedColumn<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => column,
  );

  GeneratedColumn<String> get ts =>
      $composableBuilder(column: $table.ts, builder: (column) => column);

  GeneratedColumn<bool> get dirty =>
      $composableBuilder(column: $table.dirty, builder: (column) => column);
}

class $$ChatMessageRowsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $ChatMessageRowsTable,
          ChatMessageRow,
          $$ChatMessageRowsTableFilterComposer,
          $$ChatMessageRowsTableOrderingComposer,
          $$ChatMessageRowsTableAnnotationComposer,
          $$ChatMessageRowsTableCreateCompanionBuilder,
          $$ChatMessageRowsTableUpdateCompanionBuilder,
          (
            ChatMessageRow,
            BaseReferences<
              _$AppDatabase,
              $ChatMessageRowsTable,
              ChatMessageRow
            >,
          ),
          ChatMessageRow,
          PrefetchHooks Function()
        > {
  $$ChatMessageRowsTableTableManager(
    _$AppDatabase db,
    $ChatMessageRowsTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$ChatMessageRowsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$ChatMessageRowsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$ChatMessageRowsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> bot = const Value.absent(),
                Value<String> role = const Value.absent(),
                Value<String> payloadJson = const Value.absent(),
                Value<String> ts = const Value.absent(),
                Value<bool> dirty = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => ChatMessageRowsCompanion(
                id: id,
                bot: bot,
                role: role,
                payloadJson: payloadJson,
                ts: ts,
                dirty: dirty,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String bot,
                required String role,
                required String payloadJson,
                required String ts,
                Value<bool> dirty = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => ChatMessageRowsCompanion.insert(
                id: id,
                bot: bot,
                role: role,
                payloadJson: payloadJson,
                ts: ts,
                dirty: dirty,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$ChatMessageRowsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $ChatMessageRowsTable,
      ChatMessageRow,
      $$ChatMessageRowsTableFilterComposer,
      $$ChatMessageRowsTableOrderingComposer,
      $$ChatMessageRowsTableAnnotationComposer,
      $$ChatMessageRowsTableCreateCompanionBuilder,
      $$ChatMessageRowsTableUpdateCompanionBuilder,
      (
        ChatMessageRow,
        BaseReferences<_$AppDatabase, $ChatMessageRowsTable, ChatMessageRow>,
      ),
      ChatMessageRow,
      PrefetchHooks Function()
    >;
typedef $$ActivityRowsTableCreateCompanionBuilder =
    ActivityRowsCompanion Function({
      Value<int> id,
      required String kind,
      required String payloadJson,
      required String ts,
    });
typedef $$ActivityRowsTableUpdateCompanionBuilder =
    ActivityRowsCompanion Function({
      Value<int> id,
      Value<String> kind,
      Value<String> payloadJson,
      Value<String> ts,
    });

class $$ActivityRowsTableFilterComposer
    extends Composer<_$AppDatabase, $ActivityRowsTable> {
  $$ActivityRowsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<int> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get kind => $composableBuilder(
    column: $table.kind,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get ts => $composableBuilder(
    column: $table.ts,
    builder: (column) => ColumnFilters(column),
  );
}

class $$ActivityRowsTableOrderingComposer
    extends Composer<_$AppDatabase, $ActivityRowsTable> {
  $$ActivityRowsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<int> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get kind => $composableBuilder(
    column: $table.kind,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get ts => $composableBuilder(
    column: $table.ts,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$ActivityRowsTableAnnotationComposer
    extends Composer<_$AppDatabase, $ActivityRowsTable> {
  $$ActivityRowsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<int> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get kind =>
      $composableBuilder(column: $table.kind, builder: (column) => column);

  GeneratedColumn<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => column,
  );

  GeneratedColumn<String> get ts =>
      $composableBuilder(column: $table.ts, builder: (column) => column);
}

class $$ActivityRowsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $ActivityRowsTable,
          ActivityRow,
          $$ActivityRowsTableFilterComposer,
          $$ActivityRowsTableOrderingComposer,
          $$ActivityRowsTableAnnotationComposer,
          $$ActivityRowsTableCreateCompanionBuilder,
          $$ActivityRowsTableUpdateCompanionBuilder,
          (
            ActivityRow,
            BaseReferences<_$AppDatabase, $ActivityRowsTable, ActivityRow>,
          ),
          ActivityRow,
          PrefetchHooks Function()
        > {
  $$ActivityRowsTableTableManager(_$AppDatabase db, $ActivityRowsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$ActivityRowsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$ActivityRowsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$ActivityRowsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<int> id = const Value.absent(),
                Value<String> kind = const Value.absent(),
                Value<String> payloadJson = const Value.absent(),
                Value<String> ts = const Value.absent(),
              }) => ActivityRowsCompanion(
                id: id,
                kind: kind,
                payloadJson: payloadJson,
                ts: ts,
              ),
          createCompanionCallback:
              ({
                Value<int> id = const Value.absent(),
                required String kind,
                required String payloadJson,
                required String ts,
              }) => ActivityRowsCompanion.insert(
                id: id,
                kind: kind,
                payloadJson: payloadJson,
                ts: ts,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$ActivityRowsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $ActivityRowsTable,
      ActivityRow,
      $$ActivityRowsTableFilterComposer,
      $$ActivityRowsTableOrderingComposer,
      $$ActivityRowsTableAnnotationComposer,
      $$ActivityRowsTableCreateCompanionBuilder,
      $$ActivityRowsTableUpdateCompanionBuilder,
      (
        ActivityRow,
        BaseReferences<_$AppDatabase, $ActivityRowsTable, ActivityRow>,
      ),
      ActivityRow,
      PrefetchHooks Function()
    >;
typedef $$SyncStateRowsTableCreateCompanionBuilder =
    SyncStateRowsCompanion Function({
      Value<int> id,
      Value<String> lastPullJson,
      Value<String> lastSeenJson,
    });
typedef $$SyncStateRowsTableUpdateCompanionBuilder =
    SyncStateRowsCompanion Function({
      Value<int> id,
      Value<String> lastPullJson,
      Value<String> lastSeenJson,
    });

class $$SyncStateRowsTableFilterComposer
    extends Composer<_$AppDatabase, $SyncStateRowsTable> {
  $$SyncStateRowsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<int> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get lastPullJson => $composableBuilder(
    column: $table.lastPullJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get lastSeenJson => $composableBuilder(
    column: $table.lastSeenJson,
    builder: (column) => ColumnFilters(column),
  );
}

class $$SyncStateRowsTableOrderingComposer
    extends Composer<_$AppDatabase, $SyncStateRowsTable> {
  $$SyncStateRowsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<int> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get lastPullJson => $composableBuilder(
    column: $table.lastPullJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get lastSeenJson => $composableBuilder(
    column: $table.lastSeenJson,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$SyncStateRowsTableAnnotationComposer
    extends Composer<_$AppDatabase, $SyncStateRowsTable> {
  $$SyncStateRowsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<int> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get lastPullJson => $composableBuilder(
    column: $table.lastPullJson,
    builder: (column) => column,
  );

  GeneratedColumn<String> get lastSeenJson => $composableBuilder(
    column: $table.lastSeenJson,
    builder: (column) => column,
  );
}

class $$SyncStateRowsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $SyncStateRowsTable,
          SyncStateRow,
          $$SyncStateRowsTableFilterComposer,
          $$SyncStateRowsTableOrderingComposer,
          $$SyncStateRowsTableAnnotationComposer,
          $$SyncStateRowsTableCreateCompanionBuilder,
          $$SyncStateRowsTableUpdateCompanionBuilder,
          (
            SyncStateRow,
            BaseReferences<_$AppDatabase, $SyncStateRowsTable, SyncStateRow>,
          ),
          SyncStateRow,
          PrefetchHooks Function()
        > {
  $$SyncStateRowsTableTableManager(_$AppDatabase db, $SyncStateRowsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$SyncStateRowsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$SyncStateRowsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$SyncStateRowsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<int> id = const Value.absent(),
                Value<String> lastPullJson = const Value.absent(),
                Value<String> lastSeenJson = const Value.absent(),
              }) => SyncStateRowsCompanion(
                id: id,
                lastPullJson: lastPullJson,
                lastSeenJson: lastSeenJson,
              ),
          createCompanionCallback:
              ({
                Value<int> id = const Value.absent(),
                Value<String> lastPullJson = const Value.absent(),
                Value<String> lastSeenJson = const Value.absent(),
              }) => SyncStateRowsCompanion.insert(
                id: id,
                lastPullJson: lastPullJson,
                lastSeenJson: lastSeenJson,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$SyncStateRowsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $SyncStateRowsTable,
      SyncStateRow,
      $$SyncStateRowsTableFilterComposer,
      $$SyncStateRowsTableOrderingComposer,
      $$SyncStateRowsTableAnnotationComposer,
      $$SyncStateRowsTableCreateCompanionBuilder,
      $$SyncStateRowsTableUpdateCompanionBuilder,
      (
        SyncStateRow,
        BaseReferences<_$AppDatabase, $SyncStateRowsTable, SyncStateRow>,
      ),
      SyncStateRow,
      PrefetchHooks Function()
    >;
typedef $$OutboxRowsTableCreateCompanionBuilder =
    OutboxRowsCompanion Function({
      Value<int> id,
      required String op,
      required String targetId,
      required String payloadJson,
      required int createdAt,
      Value<int> attempts,
      Value<String?> lastError,
      Value<String> status,
    });
typedef $$OutboxRowsTableUpdateCompanionBuilder =
    OutboxRowsCompanion Function({
      Value<int> id,
      Value<String> op,
      Value<String> targetId,
      Value<String> payloadJson,
      Value<int> createdAt,
      Value<int> attempts,
      Value<String?> lastError,
      Value<String> status,
    });

class $$OutboxRowsTableFilterComposer
    extends Composer<_$AppDatabase, $OutboxRowsTable> {
  $$OutboxRowsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<int> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get op => $composableBuilder(
    column: $table.op,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get targetId => $composableBuilder(
    column: $table.targetId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get attempts => $composableBuilder(
    column: $table.attempts,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get lastError => $composableBuilder(
    column: $table.lastError,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnFilters(column),
  );
}

class $$OutboxRowsTableOrderingComposer
    extends Composer<_$AppDatabase, $OutboxRowsTable> {
  $$OutboxRowsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<int> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get op => $composableBuilder(
    column: $table.op,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get targetId => $composableBuilder(
    column: $table.targetId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get attempts => $composableBuilder(
    column: $table.attempts,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get lastError => $composableBuilder(
    column: $table.lastError,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$OutboxRowsTableAnnotationComposer
    extends Composer<_$AppDatabase, $OutboxRowsTable> {
  $$OutboxRowsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<int> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get op =>
      $composableBuilder(column: $table.op, builder: (column) => column);

  GeneratedColumn<String> get targetId =>
      $composableBuilder(column: $table.targetId, builder: (column) => column);

  GeneratedColumn<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => column,
  );

  GeneratedColumn<int> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<int> get attempts =>
      $composableBuilder(column: $table.attempts, builder: (column) => column);

  GeneratedColumn<String> get lastError =>
      $composableBuilder(column: $table.lastError, builder: (column) => column);

  GeneratedColumn<String> get status =>
      $composableBuilder(column: $table.status, builder: (column) => column);
}

class $$OutboxRowsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $OutboxRowsTable,
          OutboxRow,
          $$OutboxRowsTableFilterComposer,
          $$OutboxRowsTableOrderingComposer,
          $$OutboxRowsTableAnnotationComposer,
          $$OutboxRowsTableCreateCompanionBuilder,
          $$OutboxRowsTableUpdateCompanionBuilder,
          (
            OutboxRow,
            BaseReferences<_$AppDatabase, $OutboxRowsTable, OutboxRow>,
          ),
          OutboxRow,
          PrefetchHooks Function()
        > {
  $$OutboxRowsTableTableManager(_$AppDatabase db, $OutboxRowsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$OutboxRowsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$OutboxRowsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$OutboxRowsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<int> id = const Value.absent(),
                Value<String> op = const Value.absent(),
                Value<String> targetId = const Value.absent(),
                Value<String> payloadJson = const Value.absent(),
                Value<int> createdAt = const Value.absent(),
                Value<int> attempts = const Value.absent(),
                Value<String?> lastError = const Value.absent(),
                Value<String> status = const Value.absent(),
              }) => OutboxRowsCompanion(
                id: id,
                op: op,
                targetId: targetId,
                payloadJson: payloadJson,
                createdAt: createdAt,
                attempts: attempts,
                lastError: lastError,
                status: status,
              ),
          createCompanionCallback:
              ({
                Value<int> id = const Value.absent(),
                required String op,
                required String targetId,
                required String payloadJson,
                required int createdAt,
                Value<int> attempts = const Value.absent(),
                Value<String?> lastError = const Value.absent(),
                Value<String> status = const Value.absent(),
              }) => OutboxRowsCompanion.insert(
                id: id,
                op: op,
                targetId: targetId,
                payloadJson: payloadJson,
                createdAt: createdAt,
                attempts: attempts,
                lastError: lastError,
                status: status,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$OutboxRowsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $OutboxRowsTable,
      OutboxRow,
      $$OutboxRowsTableFilterComposer,
      $$OutboxRowsTableOrderingComposer,
      $$OutboxRowsTableAnnotationComposer,
      $$OutboxRowsTableCreateCompanionBuilder,
      $$OutboxRowsTableUpdateCompanionBuilder,
      (OutboxRow, BaseReferences<_$AppDatabase, $OutboxRowsTable, OutboxRow>),
      OutboxRow,
      PrefetchHooks Function()
    >;

class $AppDatabaseManager {
  final _$AppDatabase _db;
  $AppDatabaseManager(this._db);
  $$TaskRowsTableTableManager get taskRows =>
      $$TaskRowsTableTableManager(_db, _db.taskRows);
  $$ProjectRowsTableTableManager get projectRows =>
      $$ProjectRowsTableTableManager(_db, _db.projectRows);
  $$EscalationRowsTableTableManager get escalationRows =>
      $$EscalationRowsTableTableManager(_db, _db.escalationRows);
  $$ChatMessageRowsTableTableManager get chatMessageRows =>
      $$ChatMessageRowsTableTableManager(_db, _db.chatMessageRows);
  $$ActivityRowsTableTableManager get activityRows =>
      $$ActivityRowsTableTableManager(_db, _db.activityRows);
  $$SyncStateRowsTableTableManager get syncStateRows =>
      $$SyncStateRowsTableTableManager(_db, _db.syncStateRows);
  $$OutboxRowsTableTableManager get outboxRows =>
      $$OutboxRowsTableTableManager(_db, _db.outboxRows);
}

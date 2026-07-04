#!/usr/bin/env python3
"""Apply jsc2js patches to V8 10.8.168.25 source directly."""

import os
import sys

V8_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "v8") if "v8" not in os.getcwd() else "."

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def patch_code_serializer():
    """1. Bypass SanityCheck in code-serializer.cc"""
    path = os.path.join(V8_DIR, "src", "snapshot", "code-serializer.cc")
    content = read_file(path)

    old_sanity = """SerializedCodeSanityCheckResult SerializedCodeData::SanityCheck(
    uint32_t expected_source_hash) const {
  SerializedCodeSanityCheckResult result = SanityCheckWithoutSource();
  if (result != SerializedCodeSanityCheckResult::kSuccess) return result;
  return SanityCheckJustSource(expected_source_hash);
}"""
    new_sanity = """SerializedCodeSanityCheckResult SerializedCodeData::SanityCheck(
    uint32_t expected_source_hash) const {
  return SerializedCodeSanityCheckResult::kSuccess;
}"""
    if old_sanity not in content:
        print("ERROR: SanityCheck pattern not found in code-serializer.cc")
        return False
    content = content.replace(old_sanity, new_sanity)

    old_without = """SerializedCodeSanityCheckResult SerializedCodeData::SanityCheckWithoutSource()
    const {
  if (this->size_ < kHeaderSize) {
    return SerializedCodeSanityCheckResult::kInvalidHeader;
  }
  uint32_t magic_number = GetMagicNumber();
  if (magic_number != kMagicNumber) {
    return SerializedCodeSanityCheckResult::kMagicNumberMismatch;
  }
  uint32_t version_hash = GetHeaderValue(kVersionHashOffset);
  if (version_hash != Version::Hash()) {
    return SerializedCodeSanityCheckResult::kVersionMismatch;
  }
  uint32_t flags_hash = GetHeaderValue(kFlagHashOffset);
  if (flags_hash != FlagList::Hash()) {
    return SerializedCodeSanityCheckResult::kFlagsMismatch;
  }
  uint32_t payload_length = GetHeaderValue(kPayloadLengthOffset);
  uint32_t max_payload_length = this->size_ - kHeaderSize;
  if (payload_length > max_payload_length) {
    return SerializedCodeSanityCheckResult::kLengthMismatch;
  }
  if (v8_flags.verify_snapshot_checksum) {
    uint32_t checksum = GetHeaderValue(kChecksumOffset);
    if (Checksum(ChecksummedContent()) != checksum) {
      return SerializedCodeSanityCheckResult::kChecksumMismatch;
    }
  }
  return SerializedCodeSanityCheckResult::kSuccess;
}"""
    new_without = """SerializedCodeSanityCheckResult SerializedCodeData::SanityCheckWithoutSource()
    const {
  return SerializedCodeSanityCheckResult::kSuccess;
}"""
    if old_without not in content:
        print("ERROR: SanityCheckWithoutSource pattern not found")
        return False
    content = content.replace(old_without, new_without)

    write_file(path, content)
    print("OK: Patched code-serializer.cc")
    return True

def patch_deserializer():
    """2. Comment magic_number check in deserializer.cc"""
    path = os.path.join(V8_DIR, "src", "snapshot", "deserializer.cc")
    content = read_file(path)

    old_check = "  CHECK_EQ(magic_number_, SerializedData::kMagicNumber);"
    new_check = "  //CHECK_EQ(magic_number_, SerializedData::kMagicNumber);"

    if old_check not in content:
        print("ERROR: magic_number check pattern not found in deserializer.cc")
        return False
    content = content.replace(old_check, new_check)

    write_file(path, content)
    print("OK: Patched deserializer.cc")
    return True

def patch_object_deserializer():
    """3. Comment Rehash() in object-deserializer.cc"""
    path = os.path.join(V8_DIR, "src", "snapshot", "object-deserializer.cc")
    content = read_file(path)

    old_rehash = "  Rehash();"
    new_rehash = "  // Rehash();"
    if old_rehash not in content:
        print("ERROR: Rehash() pattern not found in object-deserializer.cc")
        return False
    content = content.replace(old_rehash, new_rehash)

    write_file(path, content)
    print("OK: Patched object-deserializer.cc")
    return True

def patch_d8_cc():
    """4. Add LoadJSC + Disassemble to d8.cc (V8 10.8 APIs)"""
    path = os.path.join(V8_DIR, "src", "d8", "d8.cc")
    content = read_file(path)

    old_end = """void Shell::RealmSharedSet(Local<String> property, Local<Value> value,
                            const PropertyCallbackInfo<void>& info) {
  Isolate* isolate = info.GetIsolate();
  PerIsolateData* data = PerIsolateData::Get(isolate);
  data->realm_shared_.Reset(isolate, value);
}

// Realm.takeWebSnapshot"""
    new_block = """void Shell::RealmSharedSet(Local<String> property, Local<Value> value,
                            const PropertyCallbackInfo<void>& info) {
  Isolate* isolate = info.GetIsolate();
  PerIsolateData* data = PerIsolateData::Get(isolate);
  data->realm_shared_.Reset(isolate, value);
}

#include "src/snapshot/code-serializer.h"
#include "src/objects/objects-inl.h"
#include <iostream>
#include <unordered_set>

static void DisassembleBytecode(v8::internal::Isolate* isolate,
                                v8::internal::Tagged<v8::internal::BytecodeArray> bytecode,
                                std::unordered_set<uintptr_t>& visited,
                                int depth) {
  if (depth > 100) { return; }
  uintptr_t key = reinterpret_cast<uintptr_t>(bytecode.ptr());
  if (visited.count(key)) { return; }
  visited.insert(key);

  auto consts = bytecode.constant_pool();
  for (int i = 0; consts.valid() && i < consts.length(); i++) {
    auto obj = consts.get(i);
    if (v8::internal::IsSharedFunctionInfo(obj)) {
      auto shared = v8::internal::Cast<v8::internal::SharedFunctionInfo>(obj);
      if (shared.HasBytecodeArray()) {
        DisassembleBytecode(isolate, shared.GetBytecodeArray(isolate), visited, depth + 1);
      }
    }
  }
}

void v8::Shell::LoadJSC(const v8::FunctionCallbackInfo<v8::Value>& args) {
  auto isolate = reinterpret_cast<v8::internal::Isolate*>(args.GetIsolate());
  for (int i = 0; i < args.Length(); i++) {
    v8::String::Utf8Value filename(args.GetIsolate(), args[i]);
    if (*filename == NULL) {
      args.GetIsolate()->ThrowException(v8::Exception::Error(
          v8::String::NewFromUtf8(args.GetIsolate(), "Error loading file").ToLocalChecked()));
      return;
    }
    int length = 0;
    auto filedata = reinterpret_cast<uint8_t*>(ReadChars(*filename, &length));
    if (filedata == NULL) {
      args.GetIsolate()->ThrowException(v8::Exception::Error(
          v8::String::NewFromUtf8(args.GetIsolate(), "Error reading file").ToLocalChecked()));
      return;
    }
    v8::internal::AlignedCachedData cached_data(filedata, length);
    auto source = isolate->factory()
                      ->NewStringFromUtf8(base::CStrVector("source"))
                      .ToHandleChecked();
    v8::ScriptOriginOptions origin_options;
    v8::internal::MaybeHandle<v8::internal::SharedFunctionInfo> maybe_fun =
        v8::internal::CodeSerializer::Deserialize(isolate, &cached_data, source, origin_options);

    v8::internal::Handle<v8::internal::SharedFunctionInfo> fun;
    if (!maybe_fun.ToHandle(&fun)) {
      args.GetIsolate()->ThrowException(v8::Exception::Error(
          v8::String::NewFromUtf8(args.GetIsolate(), "Deserialize failed, possibly version mismatch or invalid .jsc file").ToLocalChecked()));
      delete[] filedata;
      return;
    }

    v8::internal::PrintF("---- Starting disassembly of %s ----\n", *filename);
    fflush(stdout);

    std::unordered_set<uintptr_t> visited;
    DisassembleBytecode(isolate, fun->GetBytecodeArray(isolate), visited, 0);

    v8::internal::PrintF("---- Finished disassembly of %s ----\n", *filename);
    fflush(stdout);

    delete[] filedata;
  }
}

// Realm.takeWebSnapshot"""

    if old_end not in content:
        print("ERROR: RealmSharedSet end pattern not found in d8.cc")
        return False
    content = content.replace(old_end, new_block)

    # Add loadjsc to CreateGlobalTemplate
    old_load = """  global_template->Set(isolate, "load",
                       FunctionTemplate::New(isolate, ExecuteFile));
  global_template->Set(isolate, "setTimeout","""
    new_load = """  global_template->Set(isolate, "load",
                       FunctionTemplate::New(isolate, ExecuteFile));
  global_template->Set(
      v8::String::NewFromUtf8(isolate, "loadjsc", v8::NewStringType::kNormal)
          .ToLocalChecked(),
      v8::FunctionTemplate::New(isolate, v8::Shell::LoadJSC));
  global_template->Set(isolate, "setTimeout","""

    if old_load not in content:
        print("ERROR: CreateGlobalTemplate load pattern not found")
        return False
    content = content.replace(old_load, new_load)

    write_file(path, content)
    print("OK: Patched d8.cc")
    return True

def patch_d8_h():
    """5. Add LoadJSC declaration to d8.h"""
    path = os.path.join(V8_DIR, "src", "d8", "d8.h")
    content = read_file(path)

    old_decl = """  static void ReportException(Isolate* isolate, TryCatch* try_catch);
  static MaybeLocal<String> ReadFile(Isolate* isolate, const char* name,"""
    new_decl = """  static void ReportException(Isolate* isolate, TryCatch* try_catch);
  static void LoadJSC(const v8::FunctionCallbackInfo<v8::Value>& args);
  static MaybeLocal<String> ReadFile(Isolate* isolate, const char* name,"""

    if old_decl not in content:
        print("ERROR: ReportException pattern not found in d8.h")
        return False
    content = content.replace(old_decl, new_decl)

    write_file(path, content)
    print("OK: Patched d8.h")
    return True

def main():
    os.chdir(V8_DIR)
    results = [
        patch_code_serializer(),
        patch_deserializer(),
        patch_object_deserializer(),
        patch_d8_cc(),
        patch_d8_h(),
    ]
    if all(results):
        print("\nAll patches applied successfully!")
        return 0
    else:
        print("\nSome patches FAILED!")
        return 1

if __name__ == "__main__":
    sys.exit(main())

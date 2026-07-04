#!/usr/bin/env python3
"""Apply jsc2js patches to V8 10.8.168.25 source directly."""

import os, sys

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

    old = """SerializedCodeSanityCheckResult SerializedCodeData::SanityCheck(
    uint32_t expected_source_hash) const {
  SerializedCodeSanityCheckResult result = SanityCheckWithoutSource();
  if (result != SerializedCodeSanityCheckResult::kSuccess) return result;
  return SanityCheckJustSource(expected_source_hash);
}"""
    new = """SerializedCodeSanityCheckResult SerializedCodeData::SanityCheck(
    uint32_t expected_source_hash) const {
  return SerializedCodeSanityCheckResult::kSuccess;
}"""
    assert old in content, "SanityCheck pattern not found"
    content = content.replace(old, new)

    old2 = """SerializedCodeSanityCheckResult SerializedCodeData::SanityCheckWithoutSource()
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
    new2 = """SerializedCodeSanityCheckResult SerializedCodeData::SanityCheckWithoutSource()
    const {
  return SerializedCodeSanityCheckResult::kSuccess;
}"""
    assert old2 in content, "SanityCheckWithoutSource pattern not found"
    content = content.replace(old2, new2)

    write_file(path, content)
    print("OK: code-serializer.cc")
    return True

def patch_deserializer():
    """2. Comment magic_number check"""
    path = os.path.join(V8_DIR, "src", "snapshot", "deserializer.cc")
    content = read_file(path)
    old = "  CHECK_EQ(magic_number_, SerializedData::kMagicNumber);"
    new = "  //CHECK_EQ(magic_number_, SerializedData::kMagicNumber);"
    assert old in content, "magic_number check not found"
    content = content.replace(old, new)
    write_file(path, content)
    print("OK: deserializer.cc")
    return True

def patch_object_deserializer():
    """3. Comment Rehash()"""
    path = os.path.join(V8_DIR, "src", "snapshot", "object-deserializer.cc")
    content = read_file(path)
    old = "  Rehash();"
    new = "  // Rehash();"
    assert old in content, "Rehash() not found"
    content = content.replace(old, new)
    write_file(path, content)
    print("OK: object-deserializer.cc")
    return True

def patch_d8_cc():
    """4. Add LoadJSC to d8.cc"""
    path = os.path.join(V8_DIR, "src", "d8", "d8.cc")
    content = read_file(path)

    # Insert Disassemble + LoadJSC before Realm.takeWebSnapshot
    anchor = "// Realm.takeWebSnapshot(index, exports)"
    assert anchor in content, "Realm.takeWebSnapshot anchor not found"

    insert_code = r'''#include "src/objects/script.h"
#include "src/interpreter/bytecode-array-iterator.h"
#include <unordered_set>

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
          v8::String::NewFromUtf8(args.GetIsolate(), "Deserialize failed").ToLocalChecked()));
      delete[] filedata;
      return;
    }

    v8::internal::PrintF("---- Disassembly start ----\n");
    fflush(stdout);

    if (fun->HasBytecodeArray()) {
      auto bytecode = fun->GetBytecodeArray(isolate);
      v8::internal::StdoutStream os;
      bytecode->Disassemble(os);
    } else {
      v8::internal::PrintF("No bytecode array found.\n");
    }

    v8::internal::PrintF("---- Disassembly end ----\n");
    fflush(stdout);

    delete[] filedata;
  }
}

'''

    content = content.replace(anchor, insert_code + anchor)

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

    assert old_load in content, "CreateGlobalTemplate load pattern not found"
    content = content.replace(old_load, new_load)

    write_file(path, content)
    print("OK: d8.cc")
    return True

def patch_d8_h():
    """5. Add LoadJSC declaration"""
    path = os.path.join(V8_DIR, "src", "d8", "d8.h")
    content = read_file(path)

    old = """  static void ReportException(Isolate* isolate, TryCatch* try_catch);
  static MaybeLocal<String> ReadFile(Isolate* isolate, const char* name,"""
    new = """  static void ReportException(Isolate* isolate, TryCatch* try_catch);
  static void LoadJSC(const v8::FunctionCallbackInfo<v8::Value>& args);
  static MaybeLocal<String> ReadFile(Isolate* isolate, const char* name,"""

    assert old in content, "ReportException pattern not found"
    content = content.replace(old, new)

    write_file(path, content)
    print("OK: d8.h")
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

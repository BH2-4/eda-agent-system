`timescale 1ns/1ps
module tb;
    reg [7:0] a, b;
    wire [8:0] sum;

    adder uut(.a(a), .b(b), .sum(sum));

    initial begin
        a = 8'd10;
        b = 8'd20;
        #20;
        if (sum !== 9'd30) begin
            $display("TEST_FAIL sum_value");
            $finish;
        end
        $display("TEST_PASS 1/1");
        $finish;
    end
endmodule
